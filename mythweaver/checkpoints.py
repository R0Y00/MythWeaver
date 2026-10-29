from datetime import datetime

from sqlalchemy import delete, select, update

from .db import Checkpoint, Entity, EntityRevision, Inbox, Outbox, Submission, SubmissionRevision, FlowRun, RetiredEntity, PlayerTurn, utcnow
from .definitions import DomainError, schema_validate, validate_definition
from .service import packed


def capture(db,game):
    return {"name":game.name,"description":game.description,"definition":game.definition,
            "entities":[packed(e) for e in db.scalars(select(Entity).where(Entity.game_id==game.id))],
            "submissions":[packed(s) for s in db.scalars(select(Submission).where(Submission.game_id==game.id))],
            "retired":[packed(r) for r in db.scalars(select(RetiredEntity).where(RetiredEntity.game_id==game.id))],
            "flows":[packed(r) for r in db.scalars(select(FlowRun).where(FlowRun.game_id==game.id))]}


def checkpoint_create_in(service,db,game,label):
    game_id=game.id
    row=Checkpoint(game_id=game_id,label=label,snapshot=capture(db,game))
    db.add(row)
    db.flush()
    service.event(db,game_id,"admin","checkpoint.created",{"id":row.id,"label":label})
    return {"id":row.id,"label":row.label,"created_at":row.created_at.isoformat(),"entities":len(row.snapshot["entities"]),"submissions":len(row.snapshot["submissions"])}


def create_checkpoint(service,game_id,label):
    from uuid import uuid4
    return service.command(game_id,'checkpoint.create',{'label':label},uuid4().hex)


def checkpoint_restore_in(service,db,game,payload):
    game_id,checkpoint_id,version=game.id,payload['id'],payload['version']
    if game.version!=version:
        raise DomainError("STATE_STALE","游戏定义已变化，请刷新后再恢复",409)
    row=db.get(Checkpoint,checkpoint_id)
    if not row or row.game_id!=game_id:
        raise DomainError("NOT_FOUND","检查点不存在",404)
    snapshot=row.snapshot
    definition=validate_definition(snapshot["definition"])
    for entity in snapshot["entities"]:
        spec=definition["types"].get(entity["type_key"])
        if not spec:
            raise DomainError("INVALID_CHECKPOINT","检查点数据类型不完整")
        schema_validate(spec.get("schema",{"type":"object"}),entity["data"])
    safety=Checkpoint(game_id=game_id,label="恢复前自动保存 · "+row.label[:70],snapshot=capture(db,game))
    db.add(safety)
    for current in db.scalars(select(Entity).where(Entity.game_id==game_id)):
        revision=db.get(EntityRevision,current.id)
        if not revision:
            db.add(EntityRevision(id=current.id,last_version=current.version))
        else:
            revision.last_version=max(revision.last_version,current.version)
    db.flush()
    db.execute(delete(Entity).where(Entity.game_id==game_id))
    previous_submissions={r.id:r.version for r in db.scalars(select(Submission).where(Submission.game_id==game_id))}
    db.execute(delete(Submission).where(Submission.game_id==game_id))
    db.execute(delete(RetiredEntity).where(RetiredEntity.game_id==game_id))
    for current_run in db.scalars(select(FlowRun).where(FlowRun.game_id==game_id)):
        current_run.status,current_run.locks='superseded',[]
        current_run.version+=1
    game.name,game.description,game.definition=snapshot["name"],snapshot["description"],definition
    game.version+=1
    for saved in snapshot["entities"]:
        revision=db.get(EntityRevision,saved["id"])
        new_version=max(revision.last_version if revision else 0,saved["version"])+1
        if revision:
            revision.last_version=new_version
        else:
            db.add(EntityRevision(id=saved["id"],last_version=new_version))
        db.add(Entity(id=saved["id"],game_id=game_id,type_key=saved["type_key"],data=saved["data"],
                      visibility=saved["visibility"],version=new_version,archived=saved["archived"]))
    for saved in snapshot.get('retired',[]):
        revision=db.get(EntityRevision,saved['id'])
        version_after=max(saved['version'],revision.last_version if revision else 0)+1
        if revision:
            revision.last_version=version_after
        else:
            db.add(EntityRevision(id=saved['id'],last_version=version_after))
        db.add(RetiredEntity(id=saved['id'],game_id=game_id,snapshot=saved['snapshot'],version=version_after,reason=saved['reason'],created_at=datetime.fromisoformat(saved['created_at'])))
    for saved in snapshot["submissions"]:
        ledger=db.get(SubmissionRevision,saved['id'])
        restored_version=max(saved.get('version',1),previous_submissions.get(saved['id'],0),ledger.last_version if ledger else 0)+1
        if ledger:
            ledger.last_version=restored_version
        else:
            db.add(SubmissionRevision(id=saved['id'],last_version=restored_version))
        db.add(Submission(id=saved["id"],game_id=game_id,interaction=saved["interaction"],actor=saved["actor"],data=saved["data"],
                          status="stale" if saved["status"]=="pending" and saved.get("dependencies") else saved["status"],definition_version=game.version,result=saved["result"],
                          dependencies=saved.get("dependencies",{}),version=restored_version,created_at=datetime.fromisoformat(saved["created_at"])))
    # Restored waits never silently resume against older entity versions.
    for saved in snapshot.get('flows',[]):
        existing=db.get(FlowRun,saved['id'])
        if existing:
            restored_run=existing
        else:
            restored_run=FlowRun(id=saved['id'],game_id=game_id,workflow=saved['workflow'],actor=saved['actor'],
                                 created_at=datetime.fromisoformat(saved['created_at']),version=saved['version']+1)
            db.add(restored_run)
        for field in ('workflow','actor','submission_id','context','current_step','result'):
            setattr(restored_run,field,saved.get(field))
        restored_run.definition_version=game.version
        restored_run.status='stale' if saved['status'] in ('waiting','running') else saved['status']
        restored_run.locks,restored_run.dependencies=[],{}
        restored_run.history=[*saved['history'],{'step':saved['current_step'],'kind':'restored','at':utcnow().isoformat()}]
    db.execute(update(Inbox).where(Inbox.game_id==game_id,Inbox.status.in_(['queued','processing'])).values(status="cancelled"))
    db.execute(update(PlayerTurn).where(PlayerTurn.game_id==game_id,PlayerTurn.status=='running').values(status='cancelled'))
    db.execute(update(Outbox).where(Outbox.game_id==game_id,Outbox.status=="queued").values(status="cancelled"))
    service.event(db,game_id,"admin","checkpoint.restored",{"id":row.id,"label":row.label,"safety_checkpoint":safety.id,"version":game.version})
    result={"ok":True,"version":game.version,"safety_checkpoint":safety.id}
    return result


def restore_checkpoint(service,game_id,checkpoint_id,version,key):
    return service.command(game_id,'checkpoint.restore',{'id':checkpoint_id,'version':version},key)
