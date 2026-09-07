"""StoryWorld 后台管理系统（FastAPI + Jinja2 + SQLite 会话）。

提供登录鉴权、仪表盘、剧本管理、世界管理与用户管理界面。
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ..config import settings
from ..db import SessionLocal, init_db
from ..engine.script_dsl import validate_script
from ..engine.tick import run_tick
from ..models import AdminUser, CanonEvent, Scene, Script, User, World, WorldPlayer
from . import auth

logger = logging.getLogger("storyworld.admin")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ---------------- 中文文案映射 ----------------
SCRIPT_STATUS_LABELS = {"pending": "待审核", "approved": "已上架", "disabled": "已下架"}
SCRIPT_STATUS_COLORS = {"pending": "warning", "approved": "success", "disabled": "secondary"}
WORLD_STATUS_LABELS = {
    "recruiting": "招募中",
    "running": "进行中",
    "finished": "已完结",
    "aborted": "已中止",
}
WORLD_STATUS_COLORS = {
    "recruiting": "info",
    "running": "success",
    "finished": "secondary",
    "aborted": "danger",
}
PLAYER_STATUS_LABELS = {"alive": "存活", "dead": "已死亡", "spectator": "观察者", "left": "已离开"}
PLAYER_STATUS_COLORS = {"alive": "success", "dead": "danger", "spectator": "info", "left": "secondary"}
MODE_LABELS = {"single": "单人", "multi": "多人"}
SOURCE_LABELS = {"official": "官方", "user": "用户"}

templates.env.globals["SCRIPT_STATUS_LABELS"] = SCRIPT_STATUS_LABELS
templates.env.globals["SCRIPT_STATUS_COLORS"] = SCRIPT_STATUS_COLORS
templates.env.globals["WORLD_STATUS_LABELS"] = WORLD_STATUS_LABELS
templates.env.globals["WORLD_STATUS_COLORS"] = WORLD_STATUS_COLORS
templates.env.globals["PLAYER_STATUS_LABELS"] = PLAYER_STATUS_LABELS
templates.env.globals["PLAYER_STATUS_COLORS"] = PLAYER_STATUS_COLORS
templates.env.globals["MODE_LABELS"] = MODE_LABELS
templates.env.globals["SOURCE_LABELS"] = SOURCE_LABELS


def fmt_dt(dt) -> str:
    """格式化时间，None 显示为 -。"""
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "-"


templates.env.filters["fmt_dt"] = fmt_dt
templates.env.filters["json_pretty"] = (
    lambda v: json.dumps(v or {}, ensure_ascii=False, indent=2)
)

# 新建剧本时的示例内容（便于直接测试，可通过 DSL 校验）
SAMPLE_CONTENT_JSON = json.dumps(
    {
        "mode": "multi",
        "days": 7,
        "world": {
            "name": "迷雾小镇",
            "background": "一场浓雾突然笼罩了小镇，通讯中断，居民被困其中。",
            "countdown": "",
            "countdown_total": 0,
        },
        "player_cards": [
            {
                "id": "p1",
                "name": "林晚",
                "role": "记者",
                "personality": "敏锐好奇",
                "secret": "",
                "goal": "查明迷雾的真相",
                "stats": {"strength": 3, "agility": 3, "intellect": 4, "charm": 3, "luck": 3},
                "public_desc": "小镇报社的记者，正在调查浓雾事件。",
            },
            {
                "id": "p2",
                "name": "陈默",
                "role": "医生",
                "personality": "冷静克制",
                "secret": "",
                "goal": "救治所有被困的居民",
                "stats": {"strength": 3, "agility": 3, "intellect": 4, "charm": 3, "luck": 3},
                "public_desc": "镇卫生所的医生。",
            },
            {
                "id": "p3",
                "name": "老周",
                "role": "杂货店主",
                "personality": "精明圆滑",
                "secret": "",
                "goal": "守住自己的店铺",
                "stats": {"strength": 4, "agility": 3, "intellect": 3, "charm": 4, "luck": 3},
                "public_desc": "镇口杂货店的老板，消息灵通。",
            },
            {
                "id": "p4",
                "name": "小满",
                "role": "学生",
                "personality": "活泼乐观",
                "secret": "",
                "goal": "找到失联的家人",
                "stats": {"strength": 2, "agility": 4, "intellect": 3, "charm": 4, "luck": 4},
                "public_desc": "镇中学的学生，熟悉镇上的每一条小巷。",
            },
        ],
        "chapters": [
            {
                "title": "第一章 迷雾降临",
                "day_start": 1,
                "day_end": 3,
                "goal": "调查浓雾的来源，安顿被困的居民",
                "events": [
                    {"day": 1, "title": "浓雾降临", "desc": "镇中心传来奇怪的声响。", "magnitude": "mid"},
                    {"day": 2, "title": "物资短缺", "desc": "镇上的储备物资开始紧张。", "magnitude": "mid"},
                ],
            },
            {
                "title": "第二章 暗流涌动",
                "day_start": 4,
                "day_end": 6,
                "goal": "发现雾中的异常，找出幕后黑手",
                "events": [
                    {"day": 4, "title": "神秘来客", "desc": "一个陌生人出现在镇口。", "magnitude": "high"},
                ],
            },
            {
                "title": "第三章 真相大白",
                "day_start": 7,
                "day_end": 7,
                "goal": "揭开真相，让小镇重归平静",
                "events": [
                    {"day": 7, "title": "真相揭晓", "desc": "迷雾散去，小镇重归平静。", "magnitude": "high"},
                ],
            },
        ],
    },
    ensure_ascii=False,
    indent=2,
)


# ---------------- 工具函数 ----------------

def _redirect(url: str, msg: str | None = None, err: str | None = None) -> RedirectResponse:
    """带闪现消息（query 参数）的 303 重定向。"""
    params: dict[str, str] = {}
    if msg:
        params["msg"] = msg
    if err:
        params["err"] = err
    target = f"{url}?{urlencode(params)}" if params else url
    return RedirectResponse(target, status_code=303)


def _render(request: Request, name: str, context: dict | None = None, **kw):
    """统一渲染：自动带上闪现消息与查询参数。"""
    status_code = kw.pop("status_code", 200)
    ctx = dict(context or {})
    ctx.update(kw)
    ctx.setdefault("msg", request.query_params.get("msg", ""))
    ctx.setdefault("err", request.query_params.get("err", ""))
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def _parse_validate(content_json: str) -> tuple[list[str], dict | None]:
    """解析并校验剧本内容 JSON，返回 (错误列表, 解析结果)。"""
    text = (content_json or "").strip()
    if not text:
        return ["剧本内容不能为空"], None
    try:
        content = json.loads(text)
    except json.JSONDecodeError as e:
        return [f"剧本内容 JSON 解析失败（第 {e.lineno} 行第 {e.colno} 列）：{e.msg}"], None
    if not isinstance(content, dict):
        return ["剧本内容必须是 JSON 对象"], None
    errors = validate_script(content)
    return errors, content


def _script_form(script: Script | None, submitted: dict | None = None) -> dict:
    """构造剧本表单回显数据。"""
    if submitted is not None:
        return submitted
    if script is not None:
        return {
            "title": script.title,
            "description": script.description or "",
            "genre": script.genre or "",
            "mode": script.mode,
            "min_players": script.min_players,
            "max_players": script.max_players,
            "days": script.days,
            "content_json": json.dumps(script.content_json or {}, ensure_ascii=False, indent=2),
        }
    return {
        "title": "",
        "description": "",
        "genre": "",
        "mode": "multi",
        "min_players": 1,
        "max_players": 6,
        "days": 7,
        "content_json": SAMPLE_CONTENT_JSON,
    }


# ---------------- 鉴权依赖 ----------------

async def require_admin(request: Request) -> str:
    """读取 session cookie 校验登录态；未登录重定向到 /admin/login。"""
    token = request.cookies.get("session")
    try:
        username = await auth.get_session_username(token)
    except Exception:  # noqa: BLE001  存储异常按未登录处理
        username = None
    if not username:
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    return username


# ---------------- 生命周期 ----------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 建表 + 确保默认管理员存在（幂等）
    init_db()
    db = SessionLocal()
    try:
        admin = db.scalar(
            select(AdminUser).where(AdminUser.username == settings.admin_username)
        )
        if admin is None:
            db.add(
                AdminUser(
                    username=settings.admin_username,
                    password_hash=auth.hash_password(settings.admin_password),
                )
            )
            db.commit()
            logger.info("已创建默认管理员账号: %s", settings.admin_username)
    finally:
        db.close()
    yield


app = FastAPI(title="StoryWorld 管理后台", lifespan=lifespan)


# ---------------- 认证 ----------------

@app.get("/admin/login")
async def login_page(request: Request):
    token = request.cookies.get("session")
    if token:
        try:
            logged = await auth.get_session_username(token)
        except Exception:  # noqa: BLE001
            logged = None
        if logged:
            return RedirectResponse("/admin", status_code=303)
    return _render(request, "login.html")


@app.post("/admin/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    db = SessionLocal()
    try:
        admin = db.scalar(select(AdminUser).where(AdminUser.username == username))
    finally:
        db.close()
    if admin is None or not auth.verify_password(password, admin.password_hash):
        return _render(request, "login.html", {"error": "用户名或密码错误", "username": username}, status_code=400)
    try:
        token = await auth.create_session(username)
    except Exception:  # noqa: BLE001
        logger.exception("创建会话失败")
        return _render(
            request,
            "login.html",
            {"error": "会话服务暂不可用（本地数据库访问失败），请稍后再试", "username": username},
            status_code=500,
        )
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie("session", token, max_age=auth.SESSION_TTL, httponly=True, samesite="lax", path="/")
    return resp


@app.get("/admin/logout")
async def logout(request: Request):
    token = request.cookies.get("session")
    if token:
        try:
            await auth.destroy_session(token)
        except Exception:  # noqa: BLE001
            pass
    resp = RedirectResponse(f"/admin/login?{urlencode({'msg': '已退出登录'})}", status_code=303)
    resp.delete_cookie("session", path="/")
    return resp


# ---------------- 仪表盘 ----------------

@app.get("/admin")
async def dashboard(request: Request, username: str = Depends(require_admin)):
    db = SessionLocal()
    try:
        stats = {
            "total_scripts": db.scalar(select(func.count()).select_from(Script)) or 0,
            "approved_scripts": db.scalar(
                select(func.count()).select_from(Script).where(Script.status == "approved")
            ) or 0,
            "pending_scripts": db.scalar(
                select(func.count()).select_from(Script).where(Script.status == "pending")
            ) or 0,
            "total_worlds": db.scalar(select(func.count()).select_from(World)) or 0,
            "running_worlds": db.scalar(
                select(func.count()).select_from(World).where(World.status == "running")
            ) or 0,
            "total_users": db.scalar(select(func.count()).select_from(User)) or 0,
            "total_players": db.scalar(select(func.count()).select_from(WorldPlayer)) or 0,
        }
        events = list(db.scalars(select(CanonEvent).order_by(CanonEvent.id.desc()).limit(10)))
        events.reverse()
        world_ids = {e.world_id for e in events}
        world_titles = {}
        if world_ids:
            world_titles = {
                w.id: w.title
                for w in db.scalars(select(World).where(World.id.in_(world_ids)))
            }
    finally:
        db.close()
    return _render(
        request,
        "dashboard.html",
        {"active": "dashboard", "username": username, "stats": stats, "events": events, "world_titles": world_titles},
    )


# ---------------- 剧本管理 ----------------

@app.get("/admin/scripts")
async def scripts_list(
    request: Request,
    status: Optional[str] = None,
    mode: Optional[str] = None,
    username: str = Depends(require_admin),
):
    db = SessionLocal()
    try:
        q = select(Script).order_by(Script.id.desc())
        if status:
            q = q.where(Script.status == status)
        if mode:
            q = q.where(Script.mode == mode)
        scripts = list(db.scalars(q))
        author_ids = {s.author_id for s in scripts if s.author_id}
        users = {}
        if author_ids:
            users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(author_ids)))}
        author_names = {}
        for s in scripts:
            if s.author_id and s.author_id in users:
                u = users[s.author_id]
                author_names[s.author_id] = u.display_name or u.username or str(u.tg_id)
    finally:
        db.close()
    return _render(
        request,
        "scripts.html",
        {
            "active": "scripts",
            "username": username,
            "scripts": scripts,
            "author_names": author_names,
            "status": status or "",
            "mode": mode or "",
        },
    )


@app.get("/admin/scripts/new")
async def script_new_page(request: Request, username: str = Depends(require_admin)):
    return _render(
        request,
        "script_edit.html",
        {"active": "scripts", "username": username, "is_new": True, "script": None, "form": _script_form(None), "errors": []},
    )


@app.post("/admin/scripts/new")
async def script_new_submit(
    request: Request,
    username: str = Depends(require_admin),
    title: str = Form(...),
    description: str = Form(""),
    genre: str = Form(""),
    mode: str = Form("multi"),
    min_players: int = Form(1),
    max_players: int = Form(6),
    days: int = Form(7),
    content_json: str = Form(""),
):
    form = {
        "title": title,
        "description": description,
        "genre": genre,
        "mode": mode,
        "min_players": min_players,
        "max_players": max_players,
        "days": days,
        "content_json": content_json,
    }
    errors, content = _parse_validate(content_json)
    if not title.strip():
        errors.append("标题不能为空")
    if errors:
        return _render(
            request,
            "script_edit.html",
            {"active": "scripts", "username": username, "is_new": True, "script": None, "form": form, "errors": errors},
            status_code=400,
        )
    db = SessionLocal()
    try:
        script = Script(
            title=title.strip(),
            description=description,
            genre=genre,
            mode=mode,
            min_players=min_players,
            max_players=max_players,
            days=days,
            status="approved",  # 管理员直接创建默认上架
            source="official",
            content_json=content,
        )
        db.add(script)
        db.commit()
    finally:
        db.close()
    return _redirect("/admin/scripts", msg="剧本创建成功并已上架")


@app.get("/admin/scripts/{script_id}/edit")
async def script_edit_page(
    request: Request, script_id: int, username: str = Depends(require_admin)
):
    db = SessionLocal()
    try:
        script = db.get(Script, script_id)
        if script is None:
            return _redirect("/admin/scripts", err="剧本不存在")
        form = _script_form(script)
    finally:
        db.close()
    return _render(
        request,
        "script_edit.html",
        {"active": "scripts", "username": username, "is_new": False, "script": script, "form": form, "errors": []},
    )


@app.post("/admin/scripts/{script_id}/edit")
async def script_edit_submit(
    request: Request,
    script_id: int,
    username: str = Depends(require_admin),
    title: str = Form(...),
    description: str = Form(""),
    genre: str = Form(""),
    mode: str = Form("multi"),
    min_players: int = Form(1),
    max_players: int = Form(6),
    days: int = Form(7),
    content_json: str = Form(""),
):
    form = {
        "title": title,
        "description": description,
        "genre": genre,
        "mode": mode,
        "min_players": min_players,
        "max_players": max_players,
        "days": days,
        "content_json": content_json,
    }
    errors, content = _parse_validate(content_json)
    if not title.strip():
        errors.append("标题不能为空")
    if errors:
        return _render(
            request,
            "script_edit.html",
            {"active": "scripts", "username": username, "is_new": False, "script": None, "form": form, "errors": errors},
            status_code=400,
        )
    db = SessionLocal()
    try:
        script = db.get(Script, script_id)
        if script is None:
            return _redirect("/admin/scripts", err="剧本不存在")
        script.title = title.strip()
        script.description = description
        script.genre = genre
        script.mode = mode
        script.min_players = min_players
        script.max_players = max_players
        script.days = days
        script.content_json = content
        db.commit()  # updated_at 由模型 onupdate 自动刷新
    finally:
        db.close()
    return _redirect("/admin/scripts", msg="剧本修改已保存")


@app.post("/admin/scripts/{script_id}/approve")
async def script_approve(request: Request, script_id: int, username: str = Depends(require_admin)):
    db = SessionLocal()
    try:
        script = db.get(Script, script_id)
        if script is None:
            return _redirect("/admin/scripts", err="剧本不存在")
        if script.status != "pending":
            label = SCRIPT_STATUS_LABELS.get(script.status, script.status)
            return _redirect("/admin/scripts", err=f"该剧本当前状态为「{label}」，无需审核")
        script.status = "approved"
        db.commit()
        return _redirect("/admin/scripts", msg=f"剧本《{script.title}》已通过审核并上架")
    finally:
        db.close()


@app.post("/admin/scripts/{script_id}/reject")
async def script_reject(
    request: Request,
    script_id: int,
    reason: str = Form(""),
    username: str = Depends(require_admin),
):
    db = SessionLocal()
    try:
        script = db.get(Script, script_id)
        if script is None:
            return _redirect("/admin/scripts", err="剧本不存在")
        if script.status != "pending":
            return _redirect("/admin/scripts", err="仅待审核剧本可驳回")
        script.status = "disabled"
        if reason.strip():
            script.description = f"【驳回原因】{reason.strip()}\n{script.description or ''}".rstrip()
        db.commit()
        return _redirect("/admin/scripts", msg=f"剧本《{script.title}》已驳回并下架")
    finally:
        db.close()


@app.post("/admin/scripts/{script_id}/toggle")
async def script_toggle(request: Request, script_id: int, username: str = Depends(require_admin)):
    db = SessionLocal()
    try:
        script = db.get(Script, script_id)
        if script is None:
            return _redirect("/admin/scripts", err="剧本不存在")
        if script.status == "approved":
            script.status = "disabled"
            msg = f"剧本《{script.title}》已下架"
        elif script.status == "disabled":
            script.status = "approved"
            msg = f"剧本《{script.title}》已上架"
        else:
            return _redirect("/admin/scripts", err="仅支持在「已上架 / 已下架」之间切换")
        db.commit()
        return _redirect("/admin/scripts", msg=msg)
    finally:
        db.close()


@app.get("/admin/scripts/{script_id}/review")
async def script_review(request: Request, script_id: int, username: str = Depends(require_admin)):
    db = SessionLocal()
    try:
        script = db.get(Script, script_id)
        if script is None:
            return _redirect("/admin/scripts", err="剧本不存在")
        raw_draft = script.raw_draft or ""
        pretty = json.dumps(script.content_json or {}, ensure_ascii=False, indent=2)
    finally:
        db.close()
    return _render(
        request,
        "script_review.html",
        {"active": "scripts", "username": username, "script": script, "raw_draft": raw_draft, "pretty": pretty},
    )


@app.get("/admin/scripts/{script_id}/json")
async def script_json(request: Request, script_id: int, username: str = Depends(require_admin)):
    db = SessionLocal()
    try:
        script = db.get(Script, script_id)
        if script is None:
            return _redirect("/admin/scripts", err="剧本不存在")
        pretty = json.dumps(script.content_json or {}, ensure_ascii=False, indent=2)
    finally:
        db.close()
    return _render(
        request,
        "script_json.html",
        {"active": "scripts", "username": username, "script": script, "pretty": pretty},
    )


# ---------------- 世界管理 ----------------

@app.get("/admin/worlds")
async def worlds_list(request: Request, username: str = Depends(require_admin)):
    db = SessionLocal()
    try:
        worlds = list(
            db.scalars(
                select(World)
                .options(selectinload(World.script))
                .order_by(World.id.desc())
            )
        )
        player_counts = dict(
            db.execute(
                select(WorldPlayer.world_id, func.count()).group_by(WorldPlayer.world_id)
            ).all()
        )
        owner_ids = {w.owner_id for w in worlds}
        owners = {}
        if owner_ids:
            owners = {u.id: u for u in db.scalars(select(User).where(User.id.in_(owner_ids)))}
        world_rows = []
        total_days = {}
        for w in worlds:
            total_days[w.id] = (w.script.content_json or {}).get("days", w.script.days)
            owner = owners.get(w.owner_id)
            world_rows.append(
                {
                    "world": w,
                    "owner_name": (owner.display_name or owner.username or str(owner.tg_id)) if owner else "未知",
                    "player_count": player_counts.get(w.id, 0),
                }
            )
    finally:
        db.close()
    return _render(
        request,
        "worlds.html",
        {"active": "worlds", "username": username, "world_rows": world_rows, "total_days": total_days},
    )


@app.get("/admin/worlds/{world_id}")
async def world_detail(request: Request, world_id: int, username: str = Depends(require_admin)):
    db = SessionLocal()
    try:
        world = db.get(World, world_id)
        if world is None:
            return _redirect("/admin/worlds", err="世界不存在")
        players = list(
            db.scalars(
                select(WorldPlayer)
                .where(WorldPlayer.world_id == world_id)
                .order_by(WorldPlayer.join_order)
            )
        )
        events = list(
            db.scalars(
                select(CanonEvent)
                .where(CanonEvent.world_id == world_id)
                .order_by(CanonEvent.id.desc())
                .limit(50)
            )
        )
        events.reverse()  # 按时间正序
        scenes = list(
            db.scalars(
                select(Scene)
                .where(Scene.world_id == world_id)
                .order_by(Scene.id.desc())
                .limit(10)
            )
        )
        scenes.reverse()
        owner = db.get(User, world.owner_id)
        owner_name = (owner.display_name or owner.username or str(owner.tg_id)) if owner else "未知"
        scene_player_names = {p.user_id: p.character_name for p in players}
        content = world.script.content_json or {}
        total_days = content.get("days", world.script.days)
    finally:
        db.close()
    return _render(
        request,
        "world_detail.html",
        {
            "active": "worlds",
            "username": username,
            "world": world,
            "players": players,
            "events": events,
            "scenes": scenes,
            "owner_name": owner_name,
            "scene_player_names": scene_player_names,
            "total_days": total_days,
        },
    )


@app.post("/admin/worlds/{world_id}/tick")
async def world_tick(request: Request, world_id: int, username: str = Depends(require_admin)):
    db = SessionLocal()
    try:
        world = db.get(World, world_id)
        if world is None:
            return _redirect("/admin/worlds", err="世界不存在")
        # run_tick 是 async 函数（内部使用 本地存储 / AI），直接 await 即可
        result = await run_tick(db, world_id)
        if result is None:
            return _redirect("/admin/worlds", err="未推进：世界不在进行中或 tick 正在进行中")
        if result.world_finished:
            return _redirect("/admin/worlds", msg=f"第 {result.day} 天推进完成，世界已完结！")
        return _redirect("/admin/worlds", msg=f"第 {result.day} 天剧情已推进完成")
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.exception("世界 %s tick 失败", world_id)
        return _redirect("/admin/worlds", err=f"推进失败：{e}")
    finally:
        db.close()


@app.post("/admin/worlds/{world_id}/abort")
async def world_abort(request: Request, world_id: int, username: str = Depends(require_admin)):
    db = SessionLocal()
    try:
        world = db.get(World, world_id)
        if world is None:
            return _redirect("/admin/worlds", err="世界不存在")
        if world.status in ("finished", "aborted"):
            return _redirect("/admin/worlds", err="世界已结束，无需中止")
        world.status = "aborted"
        db.commit()
        return _redirect("/admin/worlds", msg=f"世界《{world.title}》已中止")
    finally:
        db.close()


# ---------------- 用户管理 ----------------

@app.get("/admin/users")
async def users_list(request: Request, username: str = Depends(require_admin)):
    db = SessionLocal()
    try:
        users = list(db.scalars(select(User).order_by(User.id.desc())))
    finally:
        db.close()
    return _render(
        request,
        "users.html",
        {"active": "users", "username": username, "users": users},
    )
