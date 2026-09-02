"""通道注册表：平台名 → Channel 实例。"""
from __future__ import annotations

from .base import Channel

_registry: dict[str, Channel] = {}


def register(channel: Channel) -> None:
    _registry[channel.capabilities.name] = channel


def get(name: str) -> Channel:
    return _registry[name]


def all_channels() -> list[Channel]:
    return list(_registry.values())


def default_channel() -> Channel | None:
    return _registry.get("telegram") or next(iter(_registry.values()), None)
