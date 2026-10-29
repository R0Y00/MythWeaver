"""Durable player consent. Neither an LLM call nor chat history is consent."""
from datetime import timedelta
from copy import deepcopy
from sqlalchemy import select
from pydantic import Field
from .schemas import Input
from .db import PlayerChoice,Entity,utcnow
from .definitions import DomainError


class Confirmation(Input):
    prompt:str=Field(default='请核对本次行动，确认后才会执行。',min_length=1,max_length=2000)
    ttl_seconds:int=Field(default=600,ge=1,le=86400)
    confirm_words:list[str]=Field(default_factory=lambda:['确认','确认执行','确认购买','同意'],min_length=1,max_length=20)
    cancel_words:list[str]=Field(default_factory=lambda:['取消','不买了','不用了','拒绝'],min_length=1,max_length=20)


def validate(value):
    from pydantic import ValidationError
    try:body=Confirmation.model_validate(value)
    except ValidationError:raise DomainError('INVALID_CONFIRMATION','确认设置需要提示、有效期、确认和取消用语') from None
    words=body.confirm_words+body.cancel_words
    if any(not w.strip() or len(w)>80 or w!=w.strip() for w in words) or len(set(words))!=len(words):
        raise DomainError('INVALID_CONFIRMATION','确认和取消用语必须非空、互不重复且不超过80字')
    return body


def stamp(db,game,spec,payload,actor):
    from .policy import policy_for
    from .platform_rules import read
    from .rules import targets
    from .extensions import dependencies
    from .db import PluginRelease
    deps=targets(db,game,spec,payload['data'],actor)
    deps.update(dependencies(db,game,spec,actor))
    if 'condition' in spec:
        from .conditions import evaluate
        evaluate(db,game,spec['condition'],payload['data'],deps)
    return {'game':game.version,'policy':policy_for(db,game.id)[1],'platform':read(db)['version'],
            'records':deps,'plugins':[[r.id,r.digest,r.enabled,r.status] for r in db.scalars(select(PluginRelease).where(PluginRelease.game_id==game.id).order_by(PluginRelease.id))]}


def preview(db,game,spec,payload,actor):
    """Pure declarative quote only. Never execute plugins, writes, sends or randomness."""
    from .conditions import evaluate,required_path
    from .definitions import schema_validate
    from .functions import Runtime
    from .player import authorize_target,readable
    if 'condition' in spec and not evaluate(db,game,spec['condition'],payload['data'],{}):
        raise DomainError('PRECONDITION_FAILED','当前输入或状态不满足行动条件',409)
    cache={};lines=[];unknown=False;runtime=Runtime(game.definition)
    for effect in spec.get('effects',[]):
        if effect['operation']=='plugin':unknown=True;continue
        row=db.get(Entity,required_path(payload['data'],effect['id_from']))
        authorize_target(db,game,row,actor)
        data=cache.setdefault(row.id,deepcopy(row.data));field=effect['field'];before=deepcopy(data.get(field))
        if effect['operation']=='compute':value=runtime.evaluate(effect['expression'],{'input':payload['data'],'current':data,'actor':actor})
        else:value=required_path(payload['data'],effect['value_from']) if 'value_from' in effect else deepcopy(effect['value'])
        if effect['operation']=='increment':
            from .rules import finite_number
            if not finite_number(before) or not finite_number(value):raise DomainError('INVALID_RULE_INPUT','数值变更需要有效数值')
            data[field]=before+value
        elif effect['operation']=='append':
            if not isinstance(before,list):raise DomainError('INVALID_RULE_INPUT','追加内容需要列表字段')
            data[field]=[*before,value]
        else:data[field]=value
        type_spec=game.definition['types'][row.type_key]
        schema_validate(type_spec.get('schema',{'type':'object'}),data)
        if readable(game,row,actor) and field in type_spec['player_access']['read_fields']:
            field_spec=type_spec.get('schema',{}).get('properties',{}).get(field,{})
            if field_spec.get('x-reference'):continue
            label=field_spec.get('title') or field
            after=data[field]
            if isinstance(before,list) and isinstance(after,list):text=f'{len(before)} → {len(after)} 项'
            elif isinstance(before,(int,float,str)) and isinstance(after,(int,float,str)) and len(str(before))+len(str(after))<=120:text=f'{before} → {after}'
            else:text='将更新'
            lines.append(label+'：'+text)
    if unknown or spec.get('extension') or spec.get('mode')=='flow':lines.append('插件、随机或后续流程结果在确认后结算，本次没有预执行。')
    return lines[:10]


def prepare(db,game,actor,payload,audience,conversation):
    spec=game.definition['interactions'][payload['interaction']]
    config=validate(spec['confirmation'])
    for old in db.scalars(select(PlayerChoice).where(PlayerChoice.game_id==game.id,PlayerChoice.actor==actor,
            PlayerChoice.audience==audience,PlayerChoice.conversation==conversation,PlayerChoice.status=='waiting')):old.status='superseded'
    fields=spec.get('schema',{}).get('properties',{})
    # Do not execute plugins or roll dice to construct a quote. Values are exactly
    # the validated pending input, not model-invented prices or predicted results.
    from .definitions import schema_validate
    schema_validate(spec.get('schema',{'type':'object'}),payload['data'])
    dependencies=stamp(db,game,spec,payload,actor)
    lines=[config.prompt,'行动：'+spec.get('label',payload['interaction'])]
    for key,value in payload['data'].items():
        field=fields.get(key,{})
        if field.get('x-reference'):continue
        import json
        text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
        lines.append((field.get('title') or key)+'：'+text[:300])
    lines.extend(preview(db,game,spec,payload,actor))
    lines.append('回复“'+config.confirm_words[0]+'”执行，或“'+config.cancel_words[0]+'”放弃。')
    row=PlayerChoice(game_id=game.id,actor=actor,audience=audience,conversation=conversation,payload=payload,
        dependencies=dependencies,quote='\n'.join(lines)[:8000],expires_at=utcnow()+timedelta(seconds=config.ttl_seconds))
    db.add(row);db.flush()
    return {'id':row.id,'status':'confirmation_required','interaction':payload['interaction'],'result':row.quote}


def waiting(db,game_id,actor,audience,conversation):
    return db.scalar(select(PlayerChoice).where(PlayerChoice.game_id==game_id,PlayerChoice.actor==actor,
        PlayerChoice.audience==audience,PlayerChoice.conversation==conversation,PlayerChoice.status=='waiting').order_by(PlayerChoice.created_at.desc()).limit(1))


def respond(service,db,game,actor,prompt,audience,conversation,key):
    row=waiting(db,game.id,actor,audience,conversation)
    if not row:return None
    spec=game.definition.get('interactions',{}).get(row.payload['interaction'],{})
    config=validate(spec.get('confirmation',{}))
    word=prompt.strip()
    if word not in config.confirm_words+config.cancel_words:return None
    if word in config.cancel_words:
        row.status='cancelled';return {'id':row.id,'status':'cancelled','interaction':row.payload['interaction'],'result':'已取消，没有执行本次行动。'}
    if row.expires_at<=utcnow():
        row.status='expired';return {'id':row.id,'status':'expired','interaction':row.payload['interaction'],'result':'本次确认已过期，没有执行。请重新提出行动。'}
    try:current=stamp(db,game,spec,row.payload,actor) if spec.get('confirmation') else None
    except DomainError:current=None
    if row.dependencies!=current:
        row.status='stale';return {'id':row.id,'status':'stale','interaction':row.payload['interaction'],'result':'相关规则或资源已变化，没有执行旧确认。请重新提出行动。'}
    from .player import validate_action
    validate_action(db,game,actor,row.payload)
    result=service.execute(db,game.id,actor,key,'submit',row.payload,consent=row.id)
    row.status='executed'
    return result
