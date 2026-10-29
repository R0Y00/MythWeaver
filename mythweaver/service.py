import hashlib
import json

from sqlalchemy import select

from .db import Channel, Command, Entity, EntityRevision, Event, Game, Submission, SubmissionRevision, Participant, FlowRun
from .definitions import DomainError, schema_validate, validate_definition
from .policy import check_participant, policy_for


def packed(row):
    from datetime import datetime
    return {c.name: (getattr(row, c.name).isoformat() if isinstance(getattr(row, c.name), datetime)
                     else getattr(row, c.name)) for c in row.__table__.columns}


class Service:
    def __init__(self, store):
        self.store = store

    @property
    def plugins(self):
        if not hasattr(self,'_plugins'):
            from .plugins import Plugins
            self._plugins=Plugins(self)
        return self._plugins

    def game(self, db, game_id, lock=False):
        query = select(Game).where(Game.id == game_id)
        row = db.scalar(query.with_for_update() if lock else query)
        if not row:
            raise DomainError("NOT_FOUND", "游戏不存在", 404)
        return row

    def event(self, db, game_id, actor, kind, payload):
        db.add(Event(game_id=game_id, actor=actor, kind=kind, payload=payload))

    def remember_submission_version(self,db,row):
        ledger=db.get(SubmissionRevision,row.id)
        if ledger:
            ledger.last_version=max(ledger.last_version,row.version)
        else:
            db.add(SubmissionRevision(id=row.id,last_version=row.version))

    def create_game(self, name, description):
        with self.store.session.begin() as db:
            from .game_admin import create_game_in
            return create_game_in(self,db,name,description)

    def execute(self, db, game_id, actor, key, kind, payload):
        if not key or len(key) > 120:
            raise DomainError("IDEMPOTENCY_REQUIRED", "需要有效的幂等键")
        game = self.game(db, game_id, lock=True)
        if actor != 'admin' and kind != 'submit':
            raise DomainError('FORBIDDEN','无管理权限',403)
        from .command_catalog import validate_command
        payload=validate_command(kind,payload)
        if kind == "submit":
            check_participant(db,game_id,actor)
        digest = hashlib.sha256(json.dumps([kind, payload], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        old = db.scalar(select(Command).where(Command.game_id == game_id, Command.actor == actor, Command.key == key))
        if old:
            legacy_restore=kind=='checkpoint.restore' and old.digest==hashlib.sha256(json.dumps(['checkpoint.restore',payload['id'],payload['version']]).encode()).hexdigest()
            if old.digest != digest and not legacy_restore:
                raise DomainError("IDEMPOTENCY_CONFLICT", "相同幂等键对应不同请求", 409)
            return old.result
        if actor != "admin" and kind != "submit":
            raise DomainError("FORBIDDEN", "无管理权限", 403)
        if actor=='admin':
            # Prevent queued private replies from retaining data after an access change.
            from sqlalchemy import update
            from .db import Outbox
            db.execute(update(Outbox).where(Outbox.game_id==game_id,Outbox.group_id.like('private-%'),Outbox.status=='queued').values(status='cancelled'))
        result = self.apply(db, game, actor, kind, payload)
        db.add(Command(game_id=game_id, actor=actor, key=key, digest=digest, result=result))
        return result

    def apply(self, db, game, actor, kind, p):
        from .references import validate_references
        from .game_admin import KINDS,handle
        if kind in KINDS:
            return handle(self,db,game,kind,p)
        if kind=='flow.start':
            from .workflows import start
            return start(self,db,game,actor,p)
        if kind in ('flow.resume','flow.cancel'):
            from .workflows import change
            return change(self,db,game,p,cancel=kind=='flow.cancel')
        if kind == "game.update":
            if p["version"] != game.version:
                raise DomainError("STATE_STALE", "游戏已变化，请刷新", 409)
            if not p["name"].strip():
                raise DomainError("INVALID_NAME", "游戏名称不能为空")
            before = {"name":game.name, "description":game.description}
            game.name, game.description = p["name"].strip(), p["description"]
            game.version += 1
            self.stale_flows(db,game.id)
            for submission in db.scalars(select(Submission).where(Submission.game_id == game.id, Submission.status == "pending")):
                submission.status = "stale"
                submission.version+=1
                self.remember_submission_version(db,submission)
            self.event(db, game.id, actor, kind, {"before":before, "after":{"name":game.name,"description":game.description}})
            return packed(game)
        if kind == "definition":
            if p["version"] != game.version:
                raise DomainError("STATE_STALE", "定义已变化，请刷新", 409)
            definition = validate_definition(p["definition"])
            # Every existing record must remain expressible. Archival is not deletion.
            for entity in db.scalars(select(Entity).where(Entity.game_id == game.id)):
                spec = definition["types"].get(entity.type_key)
                if not spec:
                    raise DomainError("MIGRATION_REQUIRED", "有现存数据的类型不能直接删除，可先停用", 409)
                schema_validate(spec.get("schema", {"type": "object"}), entity.data)
                if not entity.archived:
                    validate_references(db,game,spec.get('schema',{}),entity.data)
            before = game.definition
            game.definition = definition
            game.version += 1
            self.stale_flows(db,game.id)
            # Conservative invalidation: never decide using an obsolete definition.
            pending = db.scalars(select(Submission).where(Submission.game_id == game.id, Submission.status == "pending"))
            for submission in pending:
                submission.status = "stale"
                submission.version+=1
                self.remember_submission_version(db,submission)
            self.event(db, game.id, actor, "definition.changed", {"before": before, "after": definition, "version": game.version})
            return {"version": game.version, "definition": definition}
        if kind in ("entity.create", "entity.update"):
            spec = game.definition.get("types", {}).get(p.get("type_key"))
            if not spec or not spec.get("enabled", True):
                raise DomainError("TYPE_UNAVAILABLE", "类型未定义或已停用")
            schema_validate(spec.get("schema", {"type": "object"}), p["data"])
            validate_references(db,game,spec.get('schema',{}),p['data'])
            if p.get("visibility", "private") not in ("private", "public"):
                raise DomainError("INVALID_VISIBILITY", "可见范围应为 private 或 public")
            if kind == "entity.create":
                row = Entity(game_id=game.id, type_key=p["type_key"], data=p["data"], visibility=p.get("visibility", "private"))
                before = None
                db.add(row)
            else:
                row = db.get(Entity, p["id"])
                if not row or row.game_id != game.id or row.type_key != p["type_key"]:
                    raise DomainError("NOT_FOUND", "对象不存在", 404)
                if p["version"] != row.version:
                    raise DomainError("STATE_STALE", "对象已被修改", 409)
                before = packed(row)
                if p.get('archived',False) and not row.archived:
                    from .references import ensure_archivable
                    ensure_archivable(db,game,row)
                from .workflows import invalidate_dependents
                invalidate_dependents(self,db,game.id,row.id)
                row.data = p["data"]
                row.visibility = p.get("visibility", "private")
                row.archived = p.get("archived", False)
                row.version += 1
            db.flush()
            result = packed(row)
            revision=db.get(EntityRevision,row.id)
            if revision:
                revision.last_version=row.version
            else:
                db.add(EntityRevision(id=row.id,last_version=row.version))
            self.event(db, game.id, actor, kind, {"before": before, "after": result})
            return result
        if kind == "submit":
            interaction = p["interaction"]
            spec = game.definition.get("interactions", {}).get(interaction) if interaction != "_free" else None
            if interaction != "_free" and (not spec or not spec.get("enabled", True)):
                raise DomainError("INTERACTION_UNAVAILABLE", "交互不存在或未启用")
            if actor != "admin" and spec and spec.get("allow_users") and actor not in spec["allow_users"]:
                raise DomainError("INTERACTION_DENIED","你未获得此交互的执行权限",403)
            schema_validate(spec.get("schema", {"type": "object"}) if spec else {"type": "object", "properties": {"text": {"type": "string", "maxLength": 8000}}, "required": ["text"], "additionalProperties": False}, p["data"])
            validate_references(db,game,spec.get('schema',{}) if spec else {},p['data'])
            mode = spec.get("mode", "manual") if spec else policy_for(db,game.id)[0]["free_mode"]
            from .rules import targets,apply_effects
            dependencies=targets(db,game,spec or {},p["data"],actor)
            if spec and 'condition' in spec:
                from .conditions import evaluate
                if not evaluate(db,game,spec['condition'],p['data'],dependencies):
                    raise DomainError('PRECONDITION_FAILED','当前输入或状态不满足交互条件',409)
            feedback=None
            if mode!="manual":
                feedback=apply_effects(self,db,game,actor,spec or {},p["data"])
            if actor != "admin" and not db.scalar(select(Participant).where(Participant.game_id==game.id,Participant.actor==actor)):
                db.add(Participant(game_id=game.id,actor=actor))
            row = Submission(game_id=game.id, actor=actor, interaction=interaction, data=p["data"],
                             status="pending" if mode == "manual" else "applied", definition_version=game.version,dependencies=dependencies,
                             result=spec.get("response", "") if mode == "fixed" else feedback)
            db.add(row)
            db.flush()
            flow_status=None
            self.remember_submission_version(db,row)
            if mode=='flow':
                from .workflows import start
                flow_status=start(self,db,game,actor,{'workflow':spec['workflow'],'data':p['data']},row.id)['status']
            result = packed(row)
            self.event(db, game.id, actor, "submission.created", {"id": row.id, "interaction": interaction, "mode": mode})
            # Never return private definition configuration to a QQ participant.
            return {**{k: result[k] for k in ("id", "status", "interaction", "result")},**({'flow_status':flow_status} if flow_status else {})}
        if kind == "decide":
            row = db.get(Submission, p["id"])
            if not row or row.game_id != game.id:
                raise DomainError("NOT_FOUND", "事项不存在", 404)
            if row.status != "pending" or row.definition_version != game.version:
                raise DomainError("STATE_STALE", "事项已决定或定义已变化", 409)
            if p["decision"] not in ("applied", "rejected"):
                raise DomainError("INVALID_DECISION", "无效决定")
            if not isinstance(p["result"], str) or not 1 <= len(p["result"]) <= 8000:
                raise DomainError("INVALID_RESULT", "请填写决定内容")
            if p["decision"]=="applied":
                from .rules import apply_effects
                spec=game.definition.get("interactions",{}).get(row.interaction,{})
                from .workflows import check_dependencies
                check_dependencies(db,game,row.dependencies)
                if 'condition' in spec:
                    from .conditions import evaluate
                    if not evaluate(db,game,spec['condition'],row.data,{}):
                        raise DomainError('PRECONDITION_FAILED','当前状态不满足交互条件',409)
                from .extensions import dependencies as extension_dependencies
                if any(row.dependencies.get(k)!=v for k,v in extension_dependencies(db,game,spec,row.actor).items()):
                    raise DomainError('STATE_STALE','扩展读取范围已变化，请重新提交',409)
                apply_effects(self,db,game,actor,spec,row.data,extension_actor=row.actor)
            row.status, row.result = p["decision"], p["result"]
            row.version+=1
            self.remember_submission_version(db,row)
            self.event(db, game.id, actor, "submission.decided", {"id": row.id, "status": row.status, "result": row.result})
            return packed(row)
        raise DomainError("UNKNOWN_COMMAND", "不支持的操作")

    def stale_flows(self,db,game_id):
        for run in db.scalars(select(FlowRun).where(FlowRun.game_id==game_id,FlowRun.status=='waiting')):
            run.status,run.locks='stale',[]
            run.version+=1
            self.event(db,game_id,'admin','flow.stale',{'id':run.id,'reason':'定义版本已变化'})

    def command(self, game_id, kind, payload, key, actor="admin"):
        with self.store.session.begin() as db:
            return self.execute(db, game_id, actor, key, kind, payload)

    def snapshot(self, game_id):
        with self.store.session() as db:
            game = self.game(db, game_id)
            policy, policy_version = policy_for(db,game_id)
            return {"game": packed(game),"policy":policy,"policy_version":policy_version,
                    "entities": [packed(e) for e in db.scalars(select(Entity).where(Entity.game_id == game_id).order_by(Entity.id))],
                    "flows": [packed(e) for e in db.scalars(select(FlowRun).where(FlowRun.game_id==game_id).order_by(FlowRun.created_at.desc()).limit(200))],
                    "submissions": [packed(e) for e in db.scalars(select(Submission).where(Submission.game_id == game_id).order_by(Submission.created_at.desc()).limit(200))],
                    "events": [packed(e) for e in db.scalars(select(Event).where(Event.game_id == game_id).order_by(Event.seq.desc()).limit(200))],
                    "channels": [packed(e) for e in db.scalars(select(Channel).where(Channel.game_id == game_id))]}

