"""内置剧本版本升级：旧剧情保留，只有尚未结算的开场存档可自动衔接。"""
from __future__ import annotations
import copy
from sqlalchemy import select
from ..db import begin_write
from ..models import PlayerAction, Scene, Script, World, WorldPlayer


def upgrade_lighthouse(db):
    from seed import LEGACY_LIGHTHOUSE, SEED_SCRIPTS
    from . import narrative
    old_rows = list(db.scalars(select(Script).where(
        Script.title == LEGACY_LIGHTHOUSE['title'], Script.source == 'official', Script.status == 'approved')))
    if not any(row.content_json == LEGACY_LIGHTHOUSE['content_json'] for row in old_rows):
        return
    db.rollback()
    try:
        begin_write(db)
        old_rows = list(db.scalars(select(Script).where(
            Script.title == LEGACY_LIGHTHOUSE['title'], Script.source == 'official', Script.status == 'approved'
        ).execution_options(populate_existing=True)))
        old_rows = [s for s in old_rows if s.content_json == LEGACY_LIGHTHOUSE['content_json']]
        if not old_rows:
            db.rollback()
            return
        edition = SEED_SCRIPTS[1]
        versions = list(db.scalars(select(Script).where(Script.title == edition['title'], Script.source == 'official', Script.status == 'approved')))
        current = next((s for s in versions if s.content_json == edition['content_json']), None)
        if current is None:
            current = Script(**copy.deepcopy(edition), source='official', status='approved')
            db.add(current)
            db.flush()
        for old in old_rows:
            # 下架旧入口，不覆盖原内容；已结算和结束的世界继续引用旧版。
            old.status = 'disabled'
            worlds = list(db.scalars(select(World).where(World.script_id == old.id, World.status == 'running',
                                                         World.day == 1, World.last_tick_at.is_(None))
                                     .execution_options(populate_existing=True)))
            for world in worlds:
                players = list(db.scalars(select(WorldPlayer).where(WorldPlayer.world_id == world.id)
                                         .execution_options(populate_existing=True)))
                if len(players) != 1 or players[0].status != 'alive':
                    continue
                player = players[0]
                state = player.private_state or {}
                if (set(state) - {'hp', 'items', 'clues', 'notes', 'relationships', 'faction'}
                        or state.get('hp', 10) != 10 or any(state.get(k) for k in ('items', 'clues', 'notes', 'relationships', 'faction'))
                        or world.progress_json or db.scalar(select(Scene.id).where(Scene.world_id == world.id).limit(1))):
                    continue
                if db.scalar(select(PlayerAction.id).where(PlayerAction.world_id == world.id, PlayerAction.outcome.is_not(None)).limit(1)):
                    continue
                pending = list(db.scalars(select(PlayerAction.id).where(PlayerAction.world_id == world.id)))
                snapshot = {'script_id': old.id, 'day': world.day, 'private_state': copy.deepcopy(state), 'action_ids': pending}
                world.script = current
                world.title = current.title
                narrative.initialize(world, player, current.content_json)
                world.progress_json = {**world.progress_json, 'legacy_daily': snapshot}
        db.commit()
    except Exception:
        db.rollback()
        raise
