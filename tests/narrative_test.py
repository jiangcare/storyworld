"""v0.2 真实规则、事务、存档及通道回归；不需要外部服务。"""
import asyncio
import copy
import os
import random
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.db as dbmod
from tests.support import use_test_database

use_test_database()

from app.ai.narrative import Action, Plan
from app.channel.base import Channel
from app.channel.types import ChannelCapabilities, ChannelEvent
from app.engine import narrative, world_service
from app.engine.demo_story import DEMO
from app.engine.script_dsl import validate_script
from app.engine.tick import _run_tick_locked
from app.game.flow import GameFlow
from app.models import PlayerAction, Script, World, WorldPlayer


def plan(*steps):
    return Plan(actions=[Action(kind=k, target=t) for k, t in steps])


class ChannelStub(Channel):
    capabilities = ChannelCapabilities(name="test", supports_buttons=False)

    def __init__(self):
        self.sent = []

    async def send_text(self, chat_id, text):
        self.sent.append(text)
        return len(self.sent)

    async def _send_with_actions(self, chat_id, text, actions):
        return await self.send_text(chat_id, text)

    async def ack(self, ev, text="", alert=False):
        pass


class NarrativeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        dbmod.Base.metadata.drop_all(dbmod.engine)
        dbmod.init_db()
        self.db = dbmod.SessionLocal()
        self.user = world_service.get_or_create_user(self.db, 9182, platform="test")
        self.content = copy.deepcopy(DEMO)
        self.script = Script(title=DEMO["title"], mode="single", content_json=self.content)
        self.db.add(self.script)
        self.db.commit()
        self.world = world_service.create_world(self.db, self.user, self.script)
        self.player = world_service.join_world(self.db, self.world, self.user)
        world_service.start_world(self.db, self.world)
        self.world_id, self.player_id = self.world.id, self.player.id
        self.narrator = patch("app.engine.narrative.ai.narrate", AsyncMock(return_value="雨幕中，你的行动留下了痕迹。"))
        self.narrator.start()

    def tearDown(self):
        self.narrator.stop()
        self.db.close()

    def evaluate(self, steps, *, state=None, progress=None, rng=None):
        return narrative.evaluate(self.content, state or self.player.private_state,
                                  progress or self.world.progress_json["narrative"], steps, rng=rng)

    async def turn(self, steps, text="自由行动"):
        with patch("app.engine.narrative.ai.parse_plan", AsyncMock(return_value=steps)):
            return await world_service.record_action(self.db, self.world, self.player, text)

    def test_script_validation_and_model_cannot_supply_results(self):
        self.assertEqual(validate_script(self.content), [])
        with self.assertRaises(ValueError):
            Plan.model_validate({"actions": [{"kind": "interact", "target": "steal_key", "success": True}]})
        with self.assertRaises(ValueError):
            Plan.model_validate({"actions": [{"kind": "wait"}] * 7})
        self.content["narrative"]["interactions"]["steal_key"]["check"] = "invented"
        self.assertTrue(validate_script(self.content))
        self.content = copy.deepcopy(DEMO)
        self.content["narrative"]["inventory"] = {"invented": 1}
        self.assertTrue(validate_script(self.content))
        self.content["narrative"]["inventory"] = {"key": -1}
        self.assertTrue(validate_script(self.content))

    def test_real_random_failure_stops_plan_and_success_grants_once(self):
        steps = plan(("interact", "steal_key"), ("move", "dock"))
        failed, prog, receipt = self.evaluate(steps, rng=random.Random(0))
        self.assertEqual(failed["hp"], 8)
        self.assertEqual(failed["location"], "square")
        self.assertNotIn("key", failed["inventory"])
        self.assertEqual(prog["minute"], 5)
        self.assertAlmostEqual(receipt["results"][0]["check"]["chance"], .7)
        succeeded, prog, _ = self.evaluate(steps, rng=random.Random(1))
        self.assertEqual(succeeded["inventory"]["key"], 1)
        self.assertEqual(succeeded["location"], "dock")
        succeeded["location"] = "square"
        state, after, _ = self.evaluate(plan(("interact", "steal_key")), state=succeeded, progress=prog)
        self.assertEqual(state["inventory"]["key"], 1)
        self.assertEqual(after["minute"], prog["minute"])
        self.assertEqual(self.player.private_state["hp"], 10, "纯计算不得污染原状态")

    def test_missing_materials_and_conditions_change_nothing(self):
        state = copy.deepcopy(self.player.private_state)
        state["location"] = "dock"
        after, progress, receipt = self.evaluate(plan(("interact", "rescue")), state=state)
        self.assertEqual(after, state)
        self.assertEqual(progress["minute"], 0)
        self.assertFalse(receipt["results"][0]["ok"])
        _, _, receipt = self.evaluate(plan(("interact", "warn_guard")))
        self.assertFalse(receipt["results"][0]["ok"])

    def test_pause_stops_chain_and_continue_until_player_intervenes(self):
        state, prog, _ = self.evaluate(plan(*[("wait", "")] * 6))
        self.assertEqual(prog["minute"], 20)
        self.assertEqual(prog["paused"], "decision")
        _, paused, receipt = self.evaluate(plan(("wait", "")), state=state, progress=prog)
        self.assertEqual(paused["minute"], 20)
        self.assertFalse(receipt["results"][0]["ok"])
        state["location"], state["inventory"] = "dock", {"rope": 1}
        state, ended, _ = self.evaluate(plan(("interact", "rescue")), state=state, progress=prog)
        self.assertEqual(state["inventory"]["rope"], 0)
        self.assertIn("共渡", ended["ending"])
        self.assertIsNone(ended["paused"])

    def test_solo_ending_and_deadline_take_precedence(self):
        state = copy.deepcopy(self.player.private_state)
        state["location"], state["inventory"] = "dock", {"key": 1}
        state, prog, _ = self.evaluate(plan(("interact", "leave_alone")), state=state)
        self.assertIn("独行者", prog["ending"])
        self.assertEqual(state["inventory"]["key"], 0)
        state["inventory"] = {"rope": 1}
        prog = copy.deepcopy(self.world.progress_json["narrative"])
        prog["minute"] = 55
        state, prog, _ = self.evaluate(plan(("interact", "rescue")), state=state, progress=prog)
        self.assertIn("迟到的警报", prog["ending"])
        self.assertEqual(state["hp"], 0)
        self.assertNotIn("together", prog["fired"])

    async def test_missing_api_key_fails_at_call_time(self):
        from app.ai.client import LLMClient, LLMError
        with patch("app.ai.client.settings.deepseek_api_key", ""):
            client = LLMClient()
            with self.assertRaises(LLMError):
                await client.chat_json("system", "user")
            self.assertIsNone(client._client)

    async def test_full_rescue_playthrough_and_resume_from_new_session(self):
        ok, text = await self.turn(plan(("move", "archive"), ("interact", "read_records"),
                                       ("interact", "take_rope"), ("move", "dock"), ("interact", "rescue")))
        self.assertTrue(ok)
        self.assertTrue(self.world.progress_json["narrative"]["paused"])
        self.assertEqual(self.player.private_state["inventory"]["rope"], 1)
        ok, text = await self.turn(plan(("interact", "rescue")))
        self.assertTrue(ok)
        self.assertIn("共渡雨夜", self.world.progress_json["narrative"]["ending"])
        with dbmod.SessionLocal() as fresh:
            world = fresh.get(World, self.world_id)
            player = fresh.get(WorldPlayer, self.player_id)
            self.assertEqual(world.status, "finished")
            self.assertEqual(player.private_state["level"], 2)
            self.assertEqual(player.private_state["inventory"]["rope"], 0)
            self.assertEqual(len(player.private_state["clues"]), 1)
            self.assertTrue(world_service.build_player_recent(fresh, world, player))
        channel = ChannelStub()
        await GameFlow(channel).dispatch(ChannelEvent(platform="test", user_id=9182, chat_id=9182,
                                                     text="/resume", is_private=True))
        self.assertTrue(channel.sent[-1])

    async def test_narrator_runs_after_commit_and_failure_keeps_receipt(self):
        async def fail_after_inspection(*args):
            with dbmod.SessionLocal() as fresh:
                self.assertEqual(fresh.get(WorldPlayer, self.player_id).private_state["location"], "archive")
                self.assertIsNotNone(fresh.query(PlayerAction).one().outcome)
            raise RuntimeError("LLM offline")
        with patch("app.engine.narrative.ai.narrate", side_effect=fail_after_inspection):
            ok, text = await self.turn(plan(("move", "archive")))
        self.assertTrue(ok)
        self.assertIn("抵达档案室", text)
        self.assertEqual(self.db.query(PlayerAction).count(), 1)

    async def test_commit_failure_rolls_back_everything_without_narrating(self):
        with patch.object(self.db, "commit", side_effect=RuntimeError("database unavailable")), \
                patch("app.engine.narrative.ai.narrate", AsyncMock()) as narrator:
            ok, _ = await self.turn(plan(("move", "archive"), ("interact", "take_rope")))
        self.assertFalse(ok)
        narrator.assert_not_called()
        with dbmod.SessionLocal() as fresh:
            self.assertEqual(fresh.get(WorldPlayer, self.player_id).private_state["location"], "square")
            self.assertEqual(fresh.get(World, self.world_id).progress_json["narrative"]["minute"], 0)
            self.assertEqual(fresh.query(PlayerAction).count(), 0)

    async def test_invalid_ai_and_stale_plan_do_not_change_state(self):
        with patch("app.engine.narrative.ai.parse_plan", AsyncMock(side_effect=ValueError("bad JSON"))):
            ok, _ = await world_service.record_action(self.db, self.world, self.player, "让我直接获胜")
        self.assertFalse(ok)
        self.assertEqual(self.db.query(PlayerAction).count(), 0)

        async def concurrent_change(*args):
            with dbmod.SessionLocal() as other:
                world = other.get(World, self.world_id)
                progress = copy.deepcopy(world.progress_json)
                progress["narrative"]["revision"] += 1
                world.progress_json = progress
                other.commit()
            return plan(("move", "archive"))
        with patch("app.engine.narrative.ai.parse_plan", side_effect=concurrent_change):
            ok, text = await world_service.record_action(self.db, self.world, self.player, "我去档案室")
        self.assertFalse(ok)
        self.assertIn("发生了变化", text)
        self.assertEqual(self.db.query(PlayerAction).count(), 0)

    async def test_daily_tick_cannot_mutate_realtime_world(self):
        with patch("app.engine.tick.writer_ai.generate_scene", AsyncMock()) as writer:
            result = await _run_tick_locked(self.db, self.world_id)
        self.assertIsNone(result)
        writer.assert_not_called()
        self.assertEqual(self.world.day, 1)

    async def test_channel_continue_and_creation_opening(self):
        channel = ChannelStub()
        flow = GameFlow(channel)
        ev = ChannelEvent(platform="test", user_id=9182, chat_id=9182, is_private=True,
                          payload=f"mk:{self.script.id}")
        await flow.dispatch(ev)
        self.assertTrue(any("钟楼却没有敲响" in t for t in channel.sent))
        await flow.dispatch(ChannelEvent(platform="test", user_id=9182, chat_id=9182,
                                        text="/continue", is_private=True))
        self.db.expire_all()
        newest = self.db.query(World).order_by(World.id.desc()).first()
        self.assertEqual(newest.progress_json["narrative"]["minute"], 5)


if __name__ == "__main__":
    unittest.main()
