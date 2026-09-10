"""Only operator-installed packs can provide executable scripts; saves select a pinned pack."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2] / 'worlds'


class PackError(ValueError):
    pass


@dataclass
class Pack:
    root: Path
    manifest: dict
    files: dict
    digest: str

    @property
    def skill_name(self):
        return self.manifest['skill']

    @property
    def reference(self):
        return {k: self.manifest[k] for k in ('id', 'version')} | {'hash': self.digest}

    @property
    def world(self):
        return json.loads(self.files['world.json'])

    def copy_instructions(self, destination):
        folder = destination / self.skill_name
        folder.mkdir(parents=True)
        for name, body in self.files.items():
            if name.endswith('.md'):
                path = folder / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body, encoding='utf-8')

    def content(self):
        world = self.world
        return {
            **{k: self.manifest[k] for k in ('title', 'description', 'genre')},
            'mode': 'single', 'min_players': 1, 'max_players': 1, 'days': 1,
            'world_pack': self.reference,
            'world': {'name': self.manifest['title'], 'background': world['background']},
            'chapters': [{'day_start': 1, 'day_end': 1, 'title': self.manifest['title'], 'events': []}],
            'player_cards': [world['character']],
            'npcs': [{'id': k, 'name': v['name'], 'role': v['role']} for k, v in world['initial']['npcs'].items()],
            'narrative': {'version': 2, 'start': world['initial']['player']['location'],
                'opening': world['opening'], 'max_hp': self.bind_player(world['initial']['player'])['hp'],
                'locations': {k: {'name': v['name'], 'description': v['description'], 'exits': v['exits']}
                              for k, v in world['initial']['places'].items()}},
        }

    def bind_player(self, player):
        output = {}
        for name, binding in self.manifest['player_bindings'].items():
            value = player
            for key in binding['path']:
                value = value[key]
            output[name] = value + binding['offset'] if 'offset' in binding else value
        return output


def installed(pack_id):
    if not isinstance(pack_id, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,39}', pack_id):
        raise PackError('剧本包标识无法核对。')
    folder = ROOT / pack_id
    if folder.is_symlink() or not folder.is_dir():
        raise PackError('这部剧本的 Skills 尚未安装。')
    files = {}
    for path in sorted(folder.rglob('*')):
        if path.is_symlink():
            raise PackError('剧本包不能链接到外部文件。')
        if path.is_file() and path.suffix in ('.json', '.md', '.py'):
            if path.stat().st_size > 131072:
                raise PackError('剧本包文件过大。')
            files[path.relative_to(folder).as_posix()] = path.read_text(encoding='utf-8')
    try:
        manifest = json.loads(files['manifest.json'])
        if (manifest['id'] != pack_id or manifest['format'] != 1 or
                not re.fullmatch(r'[a-z][a-z0-9-]{0,49}', manifest['skill']) or
                not files['SKILL.md'].startswith('---\n') or f"name: {manifest['skill']}\n" not in files['SKILL.md']):
            raise ValueError('metadata')
        if not all(name in files and name.startswith('scripts/') and name.endswith('.py') for name in manifest['scripts'].values()):
            raise ValueError('scripts')
        if not all(name in files and name.startswith('rules/') and name.endswith('.md') for name in manifest['rules'].values()):
            raise ValueError('rules')
        json.loads(files['world.json'])
    except (KeyError, ValueError, TypeError):
        raise PackError('剧本包不完整或版本格式无效。') from None
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return Pack(folder, manifest, files, digest)


def for_content(content):
    ref = content.get('world_pack')
    if not isinstance(ref, dict):
        raise PackError('此存档不是 Skills 剧本。')
    pack = installed(ref.get('id'))
    if pack.reference != ref:
        raise PackError('存档引用的剧本版本与已安装文件不一致，暂不改写这段旅程。')
    return pack


def seeds():
    for folder in sorted(ROOT.iterdir()):
        if folder.is_dir() and (folder / 'manifest.json').is_file():
            pack = installed(folder.name)
            content = pack.content()
            yield {k: content[k] for k in ('title', 'description', 'genre', 'mode', 'min_players', 'max_players', 'days')} | {'content_json': content}
