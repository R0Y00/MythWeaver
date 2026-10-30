import asyncio
from contextlib import asynccontextmanager, suppress
import hashlib
import hmac
import secrets
import time
from typing import Literal

from fastapi import FastAPI, Header, Request, Response, WebSocket,Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from .config import ROOT, Settings
from .db import Channel, Game, Store,RetiredEntity
from .definitions import DomainError
from .web_origin import allowed as origin_allowed,secure_cookie
from .onebot import OneBot
from .service import Service, packed
from .agent import Agent
from .models import Models
from .admin import build_admin_router
from .command_catalog import request_key,catalog
from .schemas import StartFlow, ResumeFlow, CancelFlow


from .schemas import Login, NewGame, UpdateGame, Definition, EntityInput, UpdateEntity, Submit, Decision, Binding, Input


class AgentRequest(Input):
    prompt: str = Field(min_length=1)
    session_id: str | None = Field(default=None,min_length=1,max_length=36)


class GameCommand(Input):
    kind: str
    payload: dict


class DeleteGame(Input):
    version:int=Field(ge=1)
    confirmation:str=Field(min_length=1,max_length=120)


class GameCommandPlan(Input):
    commands: list[dict] = Field(min_length=1,max_length=8)
    explanation: str = Field(default='管理员通过统一命令台发起的操作',max_length=8000)


class PlanRequest(AgentRequest):
    execute: bool = False


class PlayerRequest(AgentRequest):
    actor: str = Field(pattern=r'^qq:[0-9]{1,25}$')
    key: str = Field(min_length=1,max_length=120)
    audience:Literal['private','group']='private'
    conversation_id:str=Field(default='admin-rehearsal',min_length=1,max_length=60)


def create_app(settings=None, store=None):
    settings = settings or Settings()
    settings.validate()
    store = store or Store(settings.database_url)
    service = Service(store)
    bot = OneBot(store, service, settings)
    agent = Agent(service, settings)
    models = Models(store,settings)
    from .harness import Harness
    harness=Harness(service,settings)
    harness_tasks={}
    agent_busy = asyncio.Lock()
    failures = {}

    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(store.initialize)
        await asyncio.to_thread(service.plugins.reconcile)
        task = asyncio.create_task(bot.pump())
        yield
        for running in list(harness_tasks.values()):running.cancel()
        await asyncio.gather(*harness_tasks.values(),return_exceptions=True)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        if bot.socket:
            await bot.socket.close(code=1001)
        store.engine.dispose()

    app = FastAPI(title="MythWeaver", version="0.12.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service, app.state.bot, app.state.store = service, bot, store
    from .quality_api import router as quality_router
    app.include_router(quality_router(service,settings))

    def launch_task(game_id,run_id):
        task=asyncio.create_task(harness.run(game_id,run_id))
        harness_tasks[run_id]=task
        task.add_done_callback(lambda _:harness_tasks.pop(run_id,None))

    @app.post('/api/games/{game_id}/agent/tasks')
    async def start_execution_task(game_id: str, body: AgentRequest):
        if len(harness_tasks)>=4:raise DomainError('AGENT_BUSY','最多同时运行4个执行任务',429)
        run_id=harness.create(game_id,body.prompt,body.session_id)
        launch_task(game_id,run_id)
        return harness.inspect(game_id,run_id)

    @app.get('/api/games/{game_id}/agent/tasks/{run_id}')
    def inspect_execution_task(game_id: str,run_id: str):
        return harness.inspect(game_id,run_id)

    @app.post('/api/games/{game_id}/agent/tasks/{run_id}/resume')
    async def resume_execution_task(game_id: str,run_id: str,body: AgentRequest):
        if len(harness_tasks)>=4:raise DomainError('AGENT_BUSY','执行任务已满',429)
        harness.resume(game_id,run_id,body.prompt)
        launch_task(game_id,run_id)
        return harness.inspect(game_id,run_id)

    from .approvals import ApprovalDecision
    @app.post('/api/games/{game_id}/agent/tasks/{run_id}/approvals/{execution_id}')
    async def approve_operation(game_id:str,run_id:str,execution_id:str,body:ApprovalDecision):
        if len(harness_tasks)>=4:raise DomainError('AGENT_BUSY','后台任务已满，请稍后处理审批',429)
        from .approvals import decide
        decide(harness,game_id,run_id,execution_id,body.decision,body.reason)
        launch_task(game_id,run_id)
        return harness.inspect(game_id,run_id)

    from .platform_rules import UpdateRules
    @app.get('/api/platform/rules')
    def get_platform_rules():
        from .platform_rules import read
        with store.session() as db:return read(db)

    @app.put('/api/platform/rules')
    def set_platform_rules(body:UpdateRules):
        from .platform_rules import update
        with store.session.begin() as db:return update(db,body.version,body.changes.model_dump(exclude_none=True),body.apply_scope)

    @app.get('/api/games/{game_id}/plugins')
    def list_code_plugins(game_id: str):
        return service.plugins.list(game_id)

    @app.get('/api/games/{game_id}/plugins/{release_id}')
    def inspect_code_plugin(game_id: str,release_id: str):
        return service.plugins.inspect(game_id,release_id)

    from .harness import Draft as PluginDraft,Install as PluginInstall,State as PluginState

    @app.post('/api/games/{game_id}/plugins')
    def draft_code_plugin(game_id: str,body: PluginDraft):
        return service.plugins.draft(game_id,body.key,body.manifest,body.files)

    @app.post('/api/games/{game_id}/plugins/install')
    async def install_code_plugin(game_id: str,body: PluginInstall):
        from .plugins import cancellable
        return await cancellable(service.plugins.install,game_id,body.id,body.activate)

    @app.post('/api/games/{game_id}/plugins/state')
    def state_code_plugin(game_id: str,body: PluginState):
        return service.plugins.state(game_id,body.id,body.enabled)

    @app.post('/api/games/{game_id}/player/turn')
    async def player_turn(game_id: str, body: PlayerRequest):
        # Administrative rehearsal uses exactly the player scope; sends no QQ messages.
        return await bot.player_agent.turn(game_id,body.actor,body.prompt,body.key,audience=body.audience,conversation_id='web:'+body.conversation_id)

    @app.get('/api/games/{game_id}/player/records/{actor}')
    def player_records(game_id: str, actor: str,audience:Literal['private','group']='private'):
        from .player import AUDIENCE,view
        import re
        if not re.fullmatch(r'qq:[0-9]{1,25}',actor):
            raise DomainError('INVALID_INPUT','玩家身份无效')
        token=AUDIENCE.set(audience)
        try:
            with store.session() as db:return view(service,db,service.game(db,game_id),actor)
        finally:AUDIENCE.reset(token)

    @app.get('/api/games/{game_id}/player/turns')
    def player_turns(game_id: str):
        from .db import PlayerTurn
        with store.session() as db:
            service.game(db,game_id)
            return [packed(r) for r in db.scalars(select(PlayerTurn).where(PlayerTurn.game_id==game_id).order_by(PlayerTurn.created_at.desc()).limit(50))]

    @app.get('/api/games/{game_id}/player/handoff')
    def player_handoff(game_id:str,actor:str|None=Query(default=None,pattern=r'^qq:[0-9]{1,25}$'),audience:Literal['private','group']='private',after:str|None=None,instruction_offset:int=Query(default=0,ge=0)):
        from .player_handoff import inspect
        return inspect(service,game_id,{'actor':actor,'audience':audience,'after':after,'instruction_offset':instruction_offset},settings)

    @app.get('/api/games/{game_id}/player/turns/{turn_id}')
    def player_turn_detail(game_id:str,turn_id:str):
        from .db import PlayerTurn
        with store.session() as db:
            service.game(db,game_id);row=db.get(PlayerTurn,turn_id)
            if not row or row.game_id!=game_id:raise DomainError('NOT_FOUND','玩家对话不存在',404)
            return packed(row)

    def signature(value):
        return hmac.new(settings.session_secret.encode(), value.encode(), hashlib.sha256).hexdigest()

    def valid_session(token):
        try:
            value, sig = token.rsplit(".", 1)
            expiry, _ = value.split(":", 1)
            return hmac.compare_digest(sig, signature(value)) and time.time() < int(expiry)
        except (ValueError, AttributeError):
            return False

    @app.middleware("http")
    async def security(request, call_next):
        path = request.url.path
        if path.startswith("/api/"):
            if request.method not in ("GET", "HEAD"):
                if not origin_allowed(request,settings.public_origin):
                    return JSONResponse({"code": "ORIGIN_REJECTED", "message": "不允许的请求来源"}, status_code=403)
                if request.headers.get("x-mythweaver") != "1":
                    return JSONResponse({"code": "CSRF_REJECTED", "message": "缺少请求校验"}, status_code=403)
            if path not in ("/api/login", "/api/health") and not valid_session(request.cookies.get("mw_session", "")):
                return JSONResponse({"code": "UNAUTHENTICATED", "message": "请先登录"}, status_code=401)
            if request.method not in ("GET", "HEAD"):
                # Bound request parsing and schema evaluation memory.
                body = await request.body()
                configuration=path=='/api/platform/rules' or path.endswith('/policy') or '/commands' in path or '/agent/' in path
                limit=1048576 if '/plugins' in path or configuration else 131072
                if len(body) > limit:
                    return JSONResponse({"code": "TOO_LARGE", "message": f"请求最多 {limit//1024} KB"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse({"code": exc.code, "message": exc.message}, status_code=exc.status)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        return JSONResponse({"code": "DATABASE_ERROR", "message": "数据库操作失败，未完成的事务已回滚"}, status_code=503)

    @app.post("/api/login")
    def login(body: Login, request: Request, response: Response):
        client = request.client.host if request.client else "local"
        attempts = [stamp for stamp in failures.get(client, []) if stamp > time.time() - 60]
        failures[client] = attempts
        if len(attempts) >= 10:
            raise DomainError("RATE_LIMITED", "尝试过于频繁，请稍后再试", 429)
        if not secrets.compare_digest(body.password.encode(), settings.admin_password.encode()):
            attempts.append(time.time())
            raise DomainError("LOGIN_FAILED", "密码不正确", 401)
        failures.pop(client, None)
        value = f"{int(time.time()) + 28800}:{secrets.token_hex(16)}"
        response.set_cookie("mw_session", value + "." + signature(value), httponly=True, samesite="strict", max_age=28800, secure=secure_cookie(request,settings.public_origin))
        return {"role": "admin"}

    @app.post("/api/logout")
    def logout(response: Response):
        response.delete_cookie("mw_session")
        return {"ok": True}

    @app.get("/api/health")
    def health():
        with store.session() as db:
            db.execute(text("SELECT 1"))
        return {"ok": True}

    @app.get("/api/status")
    def status():
        selected=models.available()
        return {"onebot": bot.status(), "version":"0.12.0", "agent": {"configured":bool(selected),
                "model":selected,"message":"管理助手已配置" if selected else "请在模型管理中分配管理规划模型"}}

    @app.get("/api/games")
    def games():
        with store.session() as db:
            return [packed(g) for g in db.scalars(select(Game).order_by(Game.created_at))]

    @app.post("/api/games")
    def new_game(body: NewGame):
        return service.create_game(body.name.strip(), body.description)

    @app.get("/api/games/{game_id}")
    def snapshot(game_id: str):
        return service.snapshot(game_id)

    @app.delete('/api/games/{game_id}')
    def delete_game(game_id:str,body:DeleteGame):
        from .game_lifecycle import delete_game as remove
        return remove(service,game_id,body.version,body.confirmation)

    @app.patch("/api/games/{game_id}")
    def update_game(game_id: str, body: UpdateGame, idempotency_key: str = Header()):
        return service.command(game_id, "game.update", body.model_dump(), idempotency_key)

    @app.put("/api/games/{game_id}/definition")
    def definition(game_id: str, body: Definition, idempotency_key: str = Header()):
        return service.command(game_id, "definition", body.model_dump(), idempotency_key)

    @app.post("/api/games/{game_id}/entities")
    def new_entity(game_id: str, body: EntityInput, idempotency_key: str = Header()):
        return service.command(game_id, "entity.create", body.model_dump(), idempotency_key)

    @app.put("/api/games/{game_id}/entities/{entity_id}")
    def update_entity(game_id: str, entity_id: str, body: UpdateEntity, idempotency_key: str = Header()):
        return service.command(game_id, "entity.update", {**body.model_dump(), "id": entity_id}, idempotency_key)

    @app.post("/api/games/{game_id}/submissions")
    def submit(game_id: str, body: Submit, idempotency_key: str = Header()):
        return service.command(game_id, "submit", body.model_dump(), idempotency_key)

    @app.post('/api/games/{game_id}/flows')
    def start_flow(game_id:str,body:StartFlow,idempotency_key:str=Header()):
        return service.command(game_id,'flow.start',body.model_dump(),idempotency_key)

    @app.post('/api/games/{game_id}/flows/{run_id}/resume')
    def resume_flow(game_id:str,run_id:str,body:ResumeFlow,idempotency_key:str=Header()):
        return service.command(game_id,'flow.resume',{**body.model_dump(),'id':run_id},idempotency_key)

    @app.post('/api/games/{game_id}/flows/{run_id}/preview')
    def preview_flow(game_id:str,run_id:str,body:ResumeFlow):
        from .workflows import preview_resume
        return preview_resume(service,game_id,{**body.model_dump(),'id':run_id})

    @app.post('/api/games/{game_id}/flows/{run_id}/cancel')
    def cancel_flow(game_id:str,run_id:str,body:CancelFlow,idempotency_key:str=Header()):
        return service.command(game_id,'flow.cancel',{**body.model_dump(),'id':run_id},idempotency_key)

    @app.post("/api/games/{game_id}/decisions/{submission_id}")
    def decide(game_id: str, submission_id: str, body: Decision, idempotency_key: str = Header()):
        return service.command(game_id, "decide", {**body.model_dump(), "id": submission_id}, idempotency_key)

    @app.post("/api/games/{game_id}/channels")
    def bind(game_id:str,body:Binding,idempotency_key:str|None=Header(default=None)):
        return service.command(game_id,'channel.bind',body.model_dump(),request_key('channel.bind',idempotency_key))

    @app.delete("/api/games/{game_id}/channels/{group_id}")
    def unbind(game_id:str,group_id:str,idempotency_key:str|None=Header(default=None)):
        return service.command(game_id,'channel.unbind',{'group_id':group_id},request_key('channel.unbind',idempotency_key))

    @app.get('/api/games/{game_id}/retired')
    def retired_records(game_id:str):
        with store.session() as db:
            service.game(db,game_id)
            return [packed(r) for r in db.scalars(select(RetiredEntity).where(RetiredEntity.game_id==game_id).order_by(RetiredEntity.created_at.desc()).limit(200))]

    @app.get('/api/games/{game_id}/capabilities')
    def game_capabilities(game_id:str):
        with store.session() as db:
            service.game(db,game_id)
        return {'commands':catalog(),'scope':'current_game','actor':'admin','max_plan_commands':8,
                'message':'这些是当前已实现的游戏命令；不包含模型密钥、主机、任意代码或消息发送。'}

    @app.post('/api/games/{game_id}/commands')
    def game_command(game_id:str,body:GameCommand,idempotency_key:str=Header()):
        return service.command(game_id,body.kind,body.payload,idempotency_key)

    @app.post('/api/games/{game_id}/commands/preview')
    def preview_commands(game_id:str,body:GameCommandPlan):
        return agent.prepare(game_id,'管理员命令预演',body.explanation,body.commands)

    @app.get("/api/games/{game_id}/export")
    def export(game_id: str):
        return {"format": "mythweaver.snapshot.v1", "snapshot": service.snapshot(game_id)}

    @app.post("/api/games/{game_id}/agent/plans")
    async def propose_plan(game_id: str, body: PlanRequest):
        if agent_busy.locked():
            raise DomainError("AGENT_BUSY", "已有模型请求进行中，请稍后再试", 429)
        async with agent_busy:
            return await agent.propose(game_id, body.prompt,body.execute,body.session_id)

    @app.post('/api/games/{game_id}/agent/runs/{run_id}/cancel')
    async def cancel_agent_run(game_id:str,run_id:str):
        from .db import HarnessTask
        with store.session() as db:is_harness=db.scalar(select(HarnessTask.id).where(HarnessTask.id==run_id,HarnessTask.game_id==game_id)) is not None
        if is_harness:
            return harness.cancel(game_id,run_id)
        return agent.cancel(game_id,run_id)

    @app.post("/api/games/{game_id}/agent/plans/{plan_id}/apply")
    def apply_plan(game_id: str, plan_id: str):
        return agent.apply(game_id, plan_id)

    @app.post("/api/games/{game_id}/agent/chat")
    async def assistant_chat(game_id:str,body:AgentRequest):
        if agent_busy.locked():
            raise DomainError("AGENT_BUSY","已有模型请求进行中",429)
        async with agent_busy:
            return await agent.chat(game_id,body.prompt,body.session_id)

    app.include_router(build_admin_router(store,service,settings,bot))
    from .sessions import router as sessions_router
    app.include_router(sessions_router(store,service))
    from .examples import router as example_router
    app.include_router(example_router(service))

    @app.websocket("/channels/onebot/v11/ws")
    async def onebot(ws: WebSocket):
        await bot.connection(ws)

    dist = ROOT / "apps" / "web" / "dist"
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/")
    def index():
        if not (dist / "index.html").exists():
            return JSONResponse({"message": "请先在 apps/web 执行 npm run build"}, status_code=503)
        return FileResponse(dist / "index.html")

    return app


