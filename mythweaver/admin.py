import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header
from pydantic import Field
from sqlalchemy import func, select

from .checkpoints import create_checkpoint, restore_checkpoint
from .db import AgentPlan, AgentRun, Blueprint, Checkpoint, Entity, Event, Game, GamePolicy, Inbox, ModelProvider, Outbox, Participant, PlatformConfig, Submission, SystemLog, utcnow
from .definitions import DomainError, validate_definition
from .models import Models, validate_url
from .policy import DEFAULT_POLICY, policy_for
from .schemas import Input,PolicyInput,MemberInput,CreateBlueprint as BlueprintInput
from .command_catalog import request_key
from .service import packed


class ProviderInput(Input):
    name: str = Field(min_length=1,max_length=120)
    base_url: str = Field(min_length=1,max_length=1000)
    api_key: str = Field(default="",max_length=2000)
    clear_key: bool = False
    models: list[str] = Field(default_factory=list,max_length=100)
    enabled: bool = True
    token_parameter: str = "max_tokens"
    version: int = Field(default=0,ge=0)


class RoutingInput(Input):
    routing: dict
    version: int = Field(ge=0)


class LabelInput(Input):
    label: str = Field(min_length=1,max_length=120)


class RestoreInput(Input):
    version: int = Field(ge=1)


class RetryInput(Input):
    acknowledge_duplicate_risk: bool = False


class BatchInput(Input):
    commands: list[dict] = Field(min_length=1,max_length=50)


def build_admin_router(store,service,settings,bot):
    router=APIRouter(prefix="/api")
    models=Models(store,settings)

    def log(db,kind,detail):
        db.add(SystemLog(kind=kind,detail=detail))

    @router.post("/games/{game_id}/batch")
    def batch(game_id:str,body:BatchInput,idempotency_key:str=Header()):
        from .agent import validate_commands
        from .db import Command
        import hashlib
        import json
        if not idempotency_key or len(idempotency_key)>80:
            raise DomainError("IDEMPOTENCY_REQUIRED","需要有效的批次编号")
        commands=[validate_commands([command])[0] for command in body.commands]
        digest=hashlib.sha256(json.dumps(commands,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        receipt_key="batch-envelope:"+idempotency_key
        with store.session.begin() as db:
            service.game(db,game_id,lock=True)
            old=db.scalar(select(Command).where(Command.game_id==game_id,Command.actor=="admin",Command.key==receipt_key))
            if old:
                if old.digest!=digest:
                    raise DomainError("IDEMPOTENCY_CONFLICT","批次编号对应不同操作",409)
                return old.result
            result=[]
            for index,command in enumerate(commands):
                result.append(service.execute(db,game_id,"admin",f"batch:{idempotency_key}:{index}",command["kind"],command["payload"]))
                db.flush()
            response={"results":result}
            db.add(Command(game_id=game_id,actor="admin",key=receipt_key,digest=digest,result=response))
            return response

    @router.get("/models")
    def model_config():
        return models.view()

    def save_provider(body,provider_id=None):
        url=validate_url(body.base_url)
        if body.token_parameter not in ("max_tokens","max_completion_tokens"):
            raise DomainError("INVALID_CONFIG","不支持的输出长度参数")
        names=list(dict.fromkeys(m.strip() for m in body.models if m.strip()))
        if any(len(m)>200 for m in names) or not body.name.strip():
            raise DomainError("INVALID_CONFIG","模型名称或服务商名称无效")
        with store.session.begin() as db:
            row=db.scalar(select(ModelProvider).where(ModelProvider.id==provider_id).with_for_update()) if provider_id else None
            if provider_id and not row:
                raise DomainError("NOT_FOUND","服务商不存在",404)
            if row and row.version!=body.version:
                raise DomainError("STATE_STALE","服务商设置已变化，请刷新",409)
            if not row:
                row=ModelProvider()
                db.add(row)
                row.version=0
            row.name,row.base_url,row.models,row.enabled,row.token_parameter=body.name.strip(),url,names,body.enabled,body.token_parameter
            row.version+=1
            if body.clear_key:
                row.encrypted_key=""
            elif body.api_key.strip():
                row.encrypted_key=models.cipher.encrypt(body.api_key.strip().encode()).decode()
            db.flush()
            log(db,"model.provider_saved",{"id":row.id,"name":row.name,"enabled":row.enabled})
            return models.public(row)

    @router.post("/models/providers")
    def new_provider(body:ProviderInput):
        return save_provider(body)

    @router.put("/models/providers/{provider_id}")
    def update_provider(provider_id:str,body:ProviderInput):
        return save_provider(body,provider_id)

    @router.post("/models/providers/{provider_id}/test")
    async def test_provider(provider_id:str):
        return await models.test(provider_id)

    @router.put("/models/routing")
    def save_routing(body:RoutingInput):
        if set(body.routing)-{"planner","assistant","player"}:
            raise DomainError("INVALID_CONFIG","只支持管理规划和只读助手任务")
        with store.session.begin() as db:
            config=db.scalar(select(PlatformConfig).where(PlatformConfig.key=="model_routing").with_for_update())
            if (config.version if config else 0)!=body.version:
                raise DomainError("STATE_STALE","任务分配已变化",409)
            for route in body.routing.values():
                if not isinstance(route,dict) or set(route)-{"provider_id","model","max_tokens","timeout"}:
                    raise DomainError("INVALID_CONFIG","任务分配字段无效")
                provider=db.get(ModelProvider,route.get("provider_id",""))
                if not provider or not provider.enabled or route.get("model") not in provider.models:
                    raise DomainError("INVALID_CONFIG","请选择已启用服务商中的模型")
                if type(route.get("max_tokens",4096)) is not int or not 256<=route.get("max_tokens",4096)<=16384:
                    raise DomainError("INVALID_CONFIG","输出上限为 256–16384 tokens")
                if type(route.get("timeout",60)) is not int or not 10<=route.get("timeout",60)<=120:
                    raise DomainError("INVALID_CONFIG","超时为 10–120 秒")
            if not config:
                config=PlatformConfig(key="model_routing",value=body.routing,version=1)
                db.add(config)
            else:
                config.value=body.routing
                config.version+=1
            log(db,"model.routing_saved",{"tasks":list(body.routing)})
            return {"version":config.version,"routing":config.value}

    @router.get("/games/{game_id}/policy")
    def read_policy(game_id:str):
        with store.session() as db:
            service.game(db,game_id)
            config,version=policy_for(db,game_id)
            return {**config,"version":version}

    @router.put("/games/{game_id}/policy")
    def save_policy(game_id:str,body:PolicyInput,idempotency_key:str|None=Header(default=None)):
        return service.command(game_id,'policy.update',body.model_dump(exclude_unset=True),request_key('policy.update',idempotency_key))

    @router.get("/games/{game_id}/participants")
    def participants(game_id:str):
        with store.session() as db:
            service.game(db,game_id)
            counts=dict(db.execute(select(Submission.actor,func.count()).where(Submission.game_id==game_id).group_by(Submission.actor)).all())
            return [{**packed(p),"submissions":counts.get(p.actor,0)} for p in db.scalars(select(Participant).where(Participant.game_id==game_id).order_by(Participant.created_at))]

    @router.put("/games/{game_id}/participants")
    def save_participant(game_id:str,body:MemberInput,idempotency_key:str|None=Header(default=None)):
        return service.command(game_id,'participant.update',body.model_dump(),request_key('participant.update',idempotency_key))

    @router.get("/games/{game_id}/agent/runs")
    def runs(game_id:str):
        with store.session() as db:
            service.game(db,game_id)
            return [packed(r) for r in db.scalars(select(AgentRun).where(AgentRun.game_id==game_id).order_by(AgentRun.created_at.desc()).limit(100))]

    @router.get("/games/{game_id}/agent/plans/{plan_id}")
    def get_plan(game_id:str,plan_id:str):
        with store.session() as db:
            row=db.get(AgentPlan,plan_id)
            if not row or row.game_id!=game_id:
                raise DomainError("NOT_FOUND","计划不存在",404)
            return packed(row)

    @router.post("/games/{game_id}/agent/plans/{plan_id}/cancel")
    def cancel_plan(game_id:str,plan_id:str):
        with store.session.begin() as db:
            service.game(db,game_id,lock=True)
            row=db.scalar(select(AgentPlan).where(AgentPlan.id==plan_id,AgentPlan.game_id==game_id).with_for_update())
            if not row:
                raise DomainError("NOT_FOUND","计划不存在",404)
            if row.status!="prepared":
                raise DomainError("STATE_STALE","计划已经执行或取消",409)
            row.status="cancelled"
            for run in db.scalars(select(AgentRun).where(AgentRun.plan_id==plan_id)):
                run.status="cancelled"
            return {"ok":True}

    @router.get("/games/{game_id}/checkpoints")
    def checkpoints(game_id:str):
        with store.session() as db:
            service.game(db,game_id)
            return [{"id":r.id,"label":r.label,"created_at":r.created_at.isoformat(),"entities":len(r.snapshot["entities"]),"submissions":len(r.snapshot["submissions"])} for r in db.scalars(select(Checkpoint).where(Checkpoint.game_id==game_id).order_by(Checkpoint.created_at.desc()).limit(100))]

    @router.post("/games/{game_id}/checkpoints")
    def checkpoint_create(game_id:str,body:LabelInput,idempotency_key:str|None=Header(default=None)):
        return service.command(game_id,'checkpoint.create',{'label':body.label},request_key('checkpoint.create',idempotency_key))

    @router.post("/games/{game_id}/checkpoints/{checkpoint_id}/restore")
    def checkpoint_restore(game_id:str,checkpoint_id:str,body:RestoreInput,idempotency_key:str=Header()):
        return restore_checkpoint(service,game_id,checkpoint_id,body.version,idempotency_key)

    @router.get("/blueprints")
    def blueprints():
        with store.session() as db:
            return [packed(b) for b in db.scalars(select(Blueprint).order_by(Blueprint.created_at.desc()))]

    @router.post("/blueprints")
    def blueprint_create(body:BlueprintInput):
        from .game_admin import create_blueprint_in
        with store.session.begin() as db:
            return create_blueprint_in(db,body.name,body.description,body.definition)

    @router.post("/blueprints/{blueprint_id}/instantiate")
    def blueprint_instantiate(blueprint_id:str,body:LabelInput):
        from .game_admin import instantiate_blueprint_in
        with store.session.begin() as db:
            return instantiate_blueprint_in(service,db,blueprint_id,body.label)

    @router.get("/games/{game_id}/operations")
    def operations(game_id:str):
        with store.session() as db:
            service.game(db,game_id)
            return {"inbox":[packed(r) for r in db.scalars(select(Inbox).where(Inbox.game_id==game_id).order_by(Inbox.created_at.desc()).limit(100))],
                    "outbox":[packed(r) for r in db.scalars(select(Outbox).where(Outbox.game_id==game_id).order_by(Outbox.created_at.desc()).limit(100))]}

    @router.post("/games/{game_id}/deliveries/{delivery_id}/retry")
    def retry(game_id:str,delivery_id:str,body:RetryInput):
        with store.session.begin() as db:
            service.game(db,game_id,lock=True)
            row=db.scalar(select(Outbox).where(Outbox.id==delivery_id,Outbox.game_id==game_id).with_for_update())
            if not row:
                raise DomainError("NOT_FOUND","投递不存在",404)
            if row.status not in ("failed","unknown"):
                raise DomainError("INVALID_STATE","只能重试失败或结果不确定的投递",409)
            if row.status=="unknown" and not body.acknowledge_duplicate_risk:
                raise DomainError("DUPLICATE_RISK","此投递可能已经送达，确认可能重复后才能重试",409)
            row.status="queued"
            service.event(db,game_id,"admin","delivery.retried",{"id":row.id})
            return {"ok":True}

    @router.get("/dashboard")
    def dashboard():
        with store.session() as db:
            count=lambda model,*conditions: db.scalar(select(func.count()).select_from(model).where(*conditions))
            today=utcnow().astimezone(ZoneInfo("Asia/Hong_Kong")).date()
            days=[today-timedelta(days=offset) for offset in reversed(range(7))]
            activity={str(day):0 for day in days}
            for stamp in db.scalars(select(Event.created_at).where(Event.created_at>=utcnow()-timedelta(days=8))):
                day=str(stamp.astimezone(ZoneInfo("Asia/Hong_Kong")).date())
                if day in activity:
                    activity[day]+=1
            return {"games":count(Game),"entities":count(Entity,Entity.archived==False),"pending":count(Submission,Submission.status=="pending"),
                    "runs":count(AgentRun),"queued":count(Outbox,Outbox.status=="queued"),"delivery_errors":count(Outbox,Outbox.status.in_(["failed","unknown"])),
                    "activity":[{"date":day,"count":value} for day,value in activity.items()],
                    "system_logs":[packed(r) for r in db.scalars(select(SystemLog).order_by(SystemLog.seq.desc()).limit(50))]}

    return router
