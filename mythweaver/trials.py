"""Persistent play state, restored inside rollback transactions; no history replay."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4
from typing import Literal
from pydantic import Field
from sqlalchemy import select,delete
from sqlalchemy.orm import sessionmaker
from .db import GameTrial,Entity,Submission,FlowRun,RetiredEntity,PlayerTurn,PlayerChoice,GamePolicy,utcnow
from .schemas import Input
from .definitions import DomainError
from .service import Service,packed
from .checkpoints import capture
from .quality import config_stamp


class TrialCreate(Input):
    label:str=Field(default='隔离试玩',min_length=1,max_length=120)
    allow_external:bool=False


class TrialTurn(Input):
    id:str
    version:int=Field(ge=1)
    actor:str=Field(pattern=r'^qq:[0-9]{1,25}$')
    audience:Literal['private','group']='private'
    prompt:str=Field(min_length=1,max_length=8000)
    key:str=Field(min_length=1,max_length=120)


class TrialChange(Input):
    id:str
    version:int=Field(ge=1)
    action:Literal['reset','discard']


def state(db,game):
    result=capture(db,game)
    result['choices']=[packed(r) for r in db.scalars(select(PlayerChoice).where(PlayerChoice.game_id==game.id))]
    result['turns']=[packed(r) for r in db.scalars(select(PlayerTurn).where(PlayerTurn.game_id==game.id))]
    return result


def restore(db,game,value):
    # All modifications are visible only on the private connection until rollback.
    for model in (Entity,Submission,FlowRun,RetiredEntity,PlayerChoice,PlayerTurn):
        db.execute(delete(model).where(model.game_id==game.id))
    game.name=value['name'];game.description=value['description'];game.definition=value['definition']
    for key,model in (('entities',Entity),('submissions',Submission),('flows',FlowRun),('retired',RetiredEntity),('choices',PlayerChoice),('turns',PlayerTurn)):
        for item in value.get(key,[]):
            data=deepcopy(item)
            for datekey in ('created_at','expires_at'):
                if isinstance(data.get(datekey),str):data[datekey]=datetime.fromisoformat(data[datekey])
            db.add(model(**data))
    db.flush()


def summary(row):
    baseline={r['id']:r for r in row.baseline['entities']};changed=[]
    for record in row.state['entities']:
        before=baseline.get(record['id'])
        if not before or before['data']!=record['data']:
            changed.append({'id':record['id'],'type_key':record['type_key'],'before':before['data'] if before else None,'after':record['data']})
    return {k:v for k,v in packed(row).items() if k not in ('state','baseline')}|{'changes':changed,
        'notice':'隔离数据按每次真实结果持续保存；未写入正式游戏、未发送QQ。宿主插件文件和网络操作仅在显式允许时运行，且不能回滚。'}


def create(service,game_id,arguments):
    args=TrialCreate.model_validate(arguments)
    with service.store.session.begin() as db:
        game=service.game(db,game_id,lock=True)
        snapshot=state(db,game)
        # Fresh test conversations and consent must not inherit production consent.
        snapshot['turns']=[];snapshot['choices']=[]
        row=GameTrial(game_id=game_id,label=args.label,allow_external=args.allow_external,source_stamp=config_stamp(db,game),baseline=snapshot,state=snapshot)
        db.add(row);db.flush()
        return summary(row)


def listing(service,game_id):
    with service.store.session() as db:
        service.game(db,game_id)
        return {'trials':[summary(r) for r in db.scalars(select(GameTrial).where(GameTrial.game_id==game_id).order_by(GameTrial.created_at.desc()))]}


def change(service,game_id,arguments):
    args=TrialChange.model_validate(arguments)
    with service.store.session.begin() as db:
        service.game(db,game_id,lock=True)
        row=db.get(GameTrial,args.id)
        if not row or row.game_id!=game_id:raise DomainError('NOT_FOUND','试玩空间不存在',404)
        if row.version!=args.version:raise DomainError('STATE_STALE','试玩空间已变化',409)
        if args.action=='discard':db.delete(row);return {'discarded':True}
        row.state=deepcopy(row.baseline);row.turns=[];row.version+=1
        return summary(row)


async def turn(service,settings,game_id,arguments):
    args=TrialTurn.model_validate(arguments)
    from .player import PlayerAgent
    from .plugins import Plugins
    with service.store.engine.connect() as connection:
        outer=connection.begin()
        scoped=SimpleNamespace(engine=service.store.engine,session=sessionmaker(bind=connection,expire_on_commit=False,join_transaction_mode='create_savepoint'))
        trial=Service(scoped);trial._plugins=Plugins(trial,service.plugins.root)
        try:
            with scoped.session.begin() as db:
                game=trial.game(db,game_id,lock=True)
                row=db.get(GameTrial,args.id)
                if not row or row.game_id!=game_id:raise DomainError('NOT_FOUND','试玩空间不存在',404)
                for previous in row.turns:
                    if previous['key']==args.key:
                        if any(previous[k]!=getattr(args,k) for k in ('actor','audience','prompt')):raise DomainError('IDEMPOTENCY_CONFLICT','试玩请求编号对应其他内容',409)
                        return summary(row)
                if row.version!=args.version:raise DomainError('STATE_STALE','试玩空间已变化，请刷新',409)
                if row.source_stamp!=config_stamp(db,game):raise DomainError('TRIAL_CONFIG_STALE','正式规则已变化，请新建试玩空间以测试新版',409)
                oldversion=row.version;history=deepcopy(row.turns);allow=row.allow_external;restore(db,game,row.state)
                if not allow:
                    def blocked(*a,**kw):raise DomainError('TRIAL_EXTERNAL_BLOCKED','试玩默认不运行宿主插件；请新建时显式允许可信插件外部副作用',409)
                    trial._plugins.invoke=blocked
            result=await PlayerAgent(trial,settings).turn(game_id,args.actor,args.prompt,args.key,audience=args.audience,conversation_id='lab:'+args.id)
            with scoped.session() as db:
                snapshot=state(db,trial.game(db,game_id))
            entry={'actor':args.actor,'audience':args.audience,'prompt':args.prompt,'key':args.key,'result':result}
        finally:outer.rollback()
    # Commit only the isolated snapshot, never its simulated records or messages.
    with service.store.session.begin() as db:
        game=service.game(db,game_id,lock=True);row=db.get(GameTrial,args.id)
        if not row or row.game_id!=game_id or row.version!=oldversion:raise DomainError('STATE_STALE','试玩期间空间已变化，结果未保存',409)
        if row.source_stamp!=config_stamp(db,game):raise DomainError('TRIAL_CONFIG_STALE','试玩期间规则变化，结果未保存',409)
        row.state=snapshot;row.turns=history+[entry];row.version+=1;db.flush()
        return summary(row)
