"""后台认证：PBKDF2 密码哈希 + Redis 会话。"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from ..db import get_redis

_ITERATIONS = 100_000
SESSION_TTL = 12 * 3600


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt_hex, digest_hex = stored.split("$")
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
        return hmac.compare_digest(digest, expected)
    except (ValueError, AttributeError):
        return False


async def create_session(username: str) -> str:
    token = secrets.token_hex(16)
    r = await get_redis()
    await r.set(f"session:{token}", username, ex=SESSION_TTL)
    return token


async def destroy_session(token: str) -> None:
    r = await get_redis()
    await r.delete(f"session:{token}")


async def get_session_username(token: str) -> str | None:
    if not token:
        return None
    r = await get_redis()
    return await r.get(f"session:{token}")
