"""Web 通道服务：注册/房间 API + WebSocket 端点 + 聊天页面。

独立服务：python run_web.py  →  http://127.0.0.1:8081/web
与 bot 同进程（start_all.py）时，每日剧情会实时推送到在线网页。
"""
from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Cookie, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, select

from ..channel.types import parse_command
from ..db import SessionLocal, get_store, init_db
from ..game.flow import GameFlow, get_flow
from ..models import User, WebMessage, WebRoom, WebRoomMember
from .channel import WebChannel

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
SESSION_COOKIE = "sw_web"
SESSION_TTL = 7 * 24 * 3600

web_channel = WebChannel()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    if get_flow() is None:
        GameFlow(web_channel)
    logger.info("Web 通道就绪：http://127.0.0.1:8081/web")
    from ..engine import stream
    import asyncio
    from contextlib import suppress
    task = asyncio.create_task(stream.run(web_channel))
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="StoryWorld Web", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ================= 会话 =================

async def _create_session(user_id: int) -> str:
    token = secrets.token_hex(16)
    r = await get_store()
    await r.set(f"web_session:{token}", user_id, ex=SESSION_TTL)
    return token


async def _session_user_id(token: Optional[str]) -> Optional[int]:
    if not token:
        return None
    r = await get_store()
    raw = await r.get(f"web_session:{token}")
    return int(raw) if raw else None


def _get_user(db, user_id: int) -> Optional[User]:
    return db.get(User, user_id)


def _get_user_by_platform_id(db, platform_id: int) -> Optional[User]:
    return db.scalar(select(User).where(User.platform == "web", User.tg_id == platform_id))


def _next_platform_id(db) -> int:
    n = db.scalar(select(func.max(User.tg_id)).where(User.platform == "web")) or 0
    return int(n) + 1


async def _auth_ws(ws: WebSocket) -> Optional[User]:
    token = ws.cookies.get(SESSION_COOKIE)
    uid = await _session_user_id(token)
    if not uid:
        return None
    db = SessionLocal()
    try:
        return db.get(User, uid)
    finally:
        db.close()


# ================= 模型 =================

class RegisterIn(BaseModel):
    nickname: str


class RoomIn(BaseModel):
    name: str = ""


# ================= 页面 =================

@app.get("/")
@app.get("/web")
async def web_page():
    return FileResponse(STATIC_DIR / "index.html")


# ================= API =================

@app.get("/api/web/me")
async def api_me(token: Optional[str] = Cookie(None, alias=SESSION_COOKIE)):
    uid = await _session_user_id(token)
    db = SessionLocal()
    try:
        user = db.get(User, uid) if uid else None
    finally:
        db.close()
    if user is None:
        raise HTTPException(status_code=401, detail="未登录")
    return {"id": user.tg_id, "nickname": user.display_name}


@app.post("/api/web/register")
async def api_register(body: RegisterIn):
    nickname = (body.nickname or "").strip()
    if not (1 <= len(nickname) <= 20):
        raise HTTPException(status_code=400, detail="昵称需 1-20 个字符")
    db = SessionLocal()
    try:
        exists = db.scalar(
            select(User).where(User.platform == "web", User.display_name == nickname)
        )
        if exists:
            raise HTTPException(status_code=400, detail="昵称已被使用，换一个吧")
        user = User(
            platform="web",
            tg_id=_next_platform_id(db),
            username=nickname,
            display_name=nickname,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        token = await _create_session(user.id)
    finally:
        db.close()
    from fastapi.responses import JSONResponse

    resp = JSONResponse({"id": user.tg_id, "nickname": nickname})
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, max_age=SESSION_TTL, samesite="lax")
    return resp


@app.post("/api/web/logout")
async def api_logout(token: Optional[str] = Cookie(None, alias=SESSION_COOKIE)):
    from fastapi.responses import JSONResponse

    if token:
        r = await get_store()
        await r.delete(f"web_session:{token}")
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get("/api/web/conv/{conv}/messages")
async def api_messages(conv: str, after: int = 0, token: Optional[str] = Cookie(None, alias=SESSION_COOKIE)):
    uid = await _session_user_id(token)
    db = SessionLocal()
    try:
        user = db.get(User, uid) if uid else None
        if user is None:
            raise HTTPException(status_code=401, detail="未登录")
        if not _conv_allowed(db, user, conv):
            raise HTTPException(status_code=403, detail="无权访问该会话")
        rows = list(
            db.scalars(
                select(WebMessage)
                .where(WebMessage.conv_key == conv, WebMessage.id > after)
                .order_by(WebMessage.id)
                .limit(100)
            )
        )
        return [
            {
                "id": r.id,
                "role": r.role,
                "sender": r.sender,
                "text": r.text,
                "actions": r.actions,
            }
            for r in rows
        ]
    finally:
        db.close()


# ================= 房间 =================

def _conv_allowed(db, user: User, conv: str) -> bool:
    kind, _, raw = conv.partition(":")
    try:
        cid = int(raw)
    except ValueError:
        return False
    if kind == "u":
        return cid == user.tg_id
    if kind == "r":
        return (
            db.scalar(
                select(WebRoomMember).where(
                    WebRoomMember.room_id == cid, WebRoomMember.user_id == user.id
                )
            )
            is not None
        )
    return False


@app.post("/api/web/room")
async def api_create_room(body: RoomIn, token: Optional[str] = Cookie(None, alias=SESSION_COOKIE)):
    uid = await _session_user_id(token)
    db = SessionLocal()
    try:
        user = db.get(User, uid) if uid else None
        if user is None:
            raise HTTPException(status_code=401, detail="未登录")
        name = (body.name or "").strip()[:20] or f"{user.display_name}的房间"
        room = WebRoom(name=name, owner_user_id=user.id)
        db.add(room)
        db.flush()
        db.add(WebRoomMember(room_id=room.id, user_id=user.id))
        db.commit()
        db.refresh(room)
        return {"id": room.id, "name": room.name, "conv": f"r:{room.id}"}
    finally:
        db.close()


@app.post("/api/web/room/{room_id}/join")
async def api_join_room(room_id: int, token: Optional[str] = Cookie(None, alias=SESSION_COOKIE)):
    uid = await _session_user_id(token)
    db = SessionLocal()
    try:
        user = db.get(User, uid) if uid else None
        if user is None:
            raise HTTPException(status_code=401, detail="未登录")
        room = db.get(WebRoom, room_id)
        if room is None:
            raise HTTPException(status_code=404, detail="房间不存在")
        if not db.scalar(
            select(WebRoomMember).where(
                WebRoomMember.room_id == room_id, WebRoomMember.user_id == user.id
            )
        ):
            db.add(WebRoomMember(room_id=room_id, user_id=user.id))
            db.commit()
        return {"id": room.id, "name": room.name, "conv": f"r:{room.id}"}
    finally:
        db.close()


@app.get("/api/web/room/{room_id}/info")
async def api_room_info(room_id: int, token: Optional[str] = Cookie(None, alias=SESSION_COOKIE)):
    uid = await _session_user_id(token)
    db = SessionLocal()
    try:
        user = db.get(User, uid) if uid else None
        if user is None:
            raise HTTPException(status_code=401, detail="未登录")
        room = db.get(WebRoom, room_id)
        if room is None:
            raise HTTPException(status_code=404, detail="房间不存在")
        members = list(
            db.execute(
                select(User.display_name).join(
                    WebRoomMember, WebRoomMember.user_id == User.id
                ).where(WebRoomMember.room_id == room_id).order_by(WebRoomMember.id)
            ).scalars()
        )
        return {"id": room.id, "name": room.name, "members": members}
    finally:
        db.close()


# ================= WebSocket =================

@app.websocket("/ws/web")
async def ws_endpoint(ws: WebSocket):
    user = await _auth_ws(ws)
    conv = ws.query_params.get("conv", "")
    if user is None:
        await ws.close(code=4401)
        return
    db = SessionLocal()
    try:
        allowed = _conv_allowed(db, user, conv)
    finally:
        db.close()
    if not allowed:
        await ws.close(code=4403)
        return

    await ws.accept()
    await web_channel.register(conv, ws)
    try:
        await ws.send_json(
            {
                "type": "hello",
                "conv": conv,
                "me": {"id": user.tg_id, "nickname": user.display_name},
            }
        )
        while True:
            data = await ws.receive_json()
            kind = data.get("type")
            if kind == "text":
                await _handle_text(ws, user, conv, str(data.get("text", ""))[:2000])
            elif kind == "action":
                payload = str(data.get("payload", ""))[:64]
                if payload:
                    ev = _build_event(user, conv, payload=payload, ws=ws)
                    await web_channel.handle_event(ev)
            elif kind == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        logger.exception("[web] ws 异常 conv=%s", conv)
    finally:
        await web_channel.unregister(conv, ws)


def _build_event(
    user: User,
    conv: str,
    text: str = "",
    payload: Optional[str] = None,
    ws=None,
):
    from ..channel.types import ChannelEvent

    kind, cid = WebChannel.conv_parts(conv)
    return ChannelEvent(
        platform="web",
        user_id=user.tg_id,
        chat_id=user.tg_id if kind == "u" else -cid,
        username=user.display_name,
        display_name=user.display_name,
        text=text,
        payload=payload,
        is_private=kind == "u",
        auto_action=True,
        reply_token=ws,
        channel=web_channel,
    )


async def _handle_text(ws, user: User, conv: str, text: str) -> None:
    if not text:
        return
    await web_channel.persist_user_message(conv, user.display_name or "玩家", text)

    cmd, arg = parse_command(text)
    if cmd == "room":
        db = SessionLocal()
        try:
            name = arg.strip()[:20] or f"{user.display_name}的房间"
            room = WebRoom(name=name, owner_user_id=user.id)
            db.add(room)
            db.flush()
            db.add(WebRoomMember(room_id=room.id, user_id=user.id))
            db.commit()
            room_id = room.id
        finally:
            db.close()
        new_conv = f"r:{room_id}"
        await web_channel.persist_sys(
            new_conv,
            f"房间「{name}」已创建。输入 /create_world 选择多人剧本，或让朋友在各自页面输入 /join_room {room_id} 加入。",
        )
        await ws.send_json({"type": "redirect", "conv": new_conv})
        return

    if cmd == "join_room":
        try:
            room_id = int(arg.strip())
        except ValueError:
            await web_channel.persist_sys(conv, "用法：/join_room <房间ID>")
            return
        db = SessionLocal()
        try:
            room = db.get(WebRoom, room_id)
            if room is None:
                await web_channel.persist_sys(conv, f"房间 {room_id} 不存在。")
                return
            if not db.scalar(
                select(WebRoomMember).where(
                    WebRoomMember.room_id == room_id, WebRoomMember.user_id == user.id
                )
            ):
                db.add(WebRoomMember(room_id=room_id, user_id=user.id))
                db.commit()
            room_name = room.name
        finally:
            db.close()
        await web_channel.persist_sys(conv, f"已加入房间「{room_name}」。")
        await web_channel.persist_sys(f"r:{room_id}", f"{user.display_name} 加入了房间。")
        await ws.send_json({"type": "redirect", "conv": f"r:{room_id}"})
        return

    if cmd == "rooms":
        db = SessionLocal()
        try:
            rows = list(
                db.execute(
                    select(WebRoom.id, WebRoom.name)
                    .join(WebRoomMember, WebRoomMember.room_id == WebRoom.id)
                    .where(WebRoomMember.user_id == user.id)
                    .order_by(WebRoom.id.desc())
                ).all()
            )
        finally:
            db.close()
        if not rows:
            await web_channel.persist_sys(conv, "你还没有加入任何房间。输入 /room 名字 创建，或 /join_room ID 加入。")
        else:
            lines = ["你加入的房间："] + [f"· #{rid} {name}（/join_room {rid}）" for rid, name in rows]
            await web_channel.persist_sys(conv, "\n".join(lines))
        return

    ev = _build_event(user, conv, text=text)
    await web_channel.handle_event(ev)
