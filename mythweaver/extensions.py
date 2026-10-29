"""Trusted game handlers return bounded transactional updates and private deliveries."""
from copy import deepcopy
import json
import re

from sqlalchemy import select

from .db import Channel, Entity, EntityRevision, Outbox
from .definitions import DomainError, schema_validate


def capabilities():
    return {'placement':'rule/manual交互的extension，或workflow effects/manual步骤的extension；不能与effects混用',
            'config':{'plugin_key':'已安装插件标识','tool':'已通过测试的工具名',
                      'reads':[{'type_key':'类型标识','fields':['字段名'],'scope':'own或game'}],
                      'writes':[{'type_key':'类型标识','fields':['可写字段'],'scope':'own或game'}],'messages':False},
            'input':{'actor':'实际发起者qq:数字','input':'交互输入；流程中是{input,answers}',
                     'records':[{'id':'记录ID','type_key':'类型','version':1,'data':'仅reads授权字段'}]},
            'output':{'updates':[{'id':'reads内记录ID','changes':{'获准字段':'新值'}}],
                      'messages':[{'recipient':'qq:数字','text':'为此受众生成的内容'}],
                      'feedback':'可选，最多8000字符，仅给本次行动发起者的实际结算反馈。无需messages授权或QQ绑定，不发送额外消息。'},
            'rules':['读取在服务端，不进入玩家模型；scope=game允许可信插件解析代号、执行跨玩家行动。',
                     '玩家触发时底层工具player_enabled必须true；writes不能改变owner_field。',
                     '消息需要messages=true，受众必须为发起者或reads记录归属，已绑定本游戏私聊且未被暂停。',
                     '输入最多200条记录/128000字符；输出最多20项修改、20条消息。',
                     '所有状态变更、消息排队与行动同事务提交；失败全部回滚，重复请求不重复排队。',
                     'manual仅在裁决通过时运行，workflow人工步骤可读取管理员answers；私聊真实投递是提交后的独立过程。',
                     'feedback由可信插件明确生成，不能直接返回隐藏records/目标答案；字段不会自动脱敏。流程步骤暂不使用feedback作为流程最终结果。',
                     '插件是管理员可信代码，负责游戏规则和各受众的正文；平台不判断文案是否泄露剧情。']}


def validate(definition, spec):
    config=spec.get('extension')
    if config is None:return
    if (not isinstance(config,dict) or set(config)-{'plugin_key','tool','reads','writes','messages'}
        or any(not isinstance(config.get(k),str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,23}',config[k]) for k in ('plugin_key','tool'))
        or not isinstance(config.get('messages',False),bool) or spec.get('effects')):
        raise DomainError('INVALID_EXTENSION','扩展需指定插件、工具、读取/写入授权；不得混合effects')
    for section in ('reads','writes'):
        grants=config.get(section,[])
        if not isinstance(grants,list) or len(grants)>20:
            raise DomainError('INVALID_EXTENSION','每类扩展授权最多20项')
        seen=set()
        for grant in grants:
            if not isinstance(grant,dict) or set(grant)!={'type_key','fields','scope'}:
                raise DomainError('INVALID_EXTENSION','授权需要type_key、fields、scope')
            key=grant['type_key']
            if not isinstance(key,str) or key not in definition.get('types',{}) or key in seen:
                raise DomainError('INVALID_EXTENSION','授权类型不存在或重复')
            seen.add(key)
            fields=grant['fields'];type_spec=definition['types'][key]
            if (grant['scope'] not in ('own','game') or not isinstance(fields,list) or not 1<=len(fields)<=100
                or any(not isinstance(f,str) or f not in type_spec.get('schema',{}).get('properties',{}) for f in fields)):
                raise DomainError('INVALID_EXTENSION','授权字段或scope无效')
            owner=type_spec.get('player_access',{}).get('owner_field')
            if grant['scope']=='own' and not owner:
                raise DomainError('INVALID_EXTENSION','own授权要求类型配置归属字段')
            if section=='writes' and owner in fields:
                raise DomainError('INVALID_EXTENSION','扩展不能通过玩家行动重分配记录归属')
    reads={g['type_key'] for g in config.get('reads',[])}
    if any(g['type_key'] not in reads for g in config.get('writes',[])):
        raise DomainError('INVALID_EXTENSION','可写类型必须同时授予读取权限')


def records(db,game,config,actor):
    result=[]
    for grant in config.get('reads',[]):
        key=grant['type_key'];spec=game.definition['types'][key]
        if not spec.get('enabled',True):raise DomainError('TYPE_UNAVAILABLE','扩展依赖类型已停用')
        statement=select(Entity).where(Entity.game_id==game.id,Entity.type_key==key,Entity.archived==False)
        if grant['scope']=='own':
            statement=statement.where(Entity.data[spec['player_access']['owner_field']].astext==actor)
        rows=list(db.scalars(statement.order_by(Entity.id).limit(201)))
        result.extend(rows)
        if len(result)>200:raise DomainError('EXTENSION_LIMIT','扩展读取超过200条记录，请缩小授权范围',409)
    return result


def dependencies(db,game,spec,actor):
    config=spec.get('extension')
    return {r.id:r.version for r in records(db,game,config,actor)} if config else {}


def apply(service,db,game,spec,data,actor,run_id=None):
    config=spec.get('extension')
    if not config:return
    rows=records(db,game,config,actor);by_id={r.id:r for r in rows}
    fields={g['type_key']:g['fields'] for g in config.get('reads',[])}
    payload={'actor':actor,'input':deepcopy(data),'records':[
        {'id':r.id,'type_key':r.type_key,'version':r.version,
         'data':{k:deepcopy(v) for k,v in r.data.items() if k in fields[r.type_key]}} for r in rows]}
    if len(json.dumps(payload,ensure_ascii=False))>128000:
        raise DomainError('EXTENSION_LIMIT','扩展输入超过128000字符',409)
    # Private server inputs and plugin errors must never become player model observations.
    try:
        result=service.plugins.invoke(game.id,config['plugin_key'],config['tool'],payload,actor=actor,db=db)
    except DomainError as exc:
        if exc.code=='TRIAL_EXTERNAL_BLOCKED':raise
        raise DomainError('EXTENSION_FAILED','自定义处理器未成功执行，请管理员检查插件及授权',409) from None
    if not isinstance(result,dict) or set(result)-{'updates','messages','feedback'}:
        raise DomainError('INVALID_EXTENSION_RESULT','处理器仅可返回updates、messages及可选feedback')
    feedback=result.get('feedback')
    if 'feedback' in result and (not isinstance(feedback,str) or not 1<=len(feedback)<=8000):
        raise DomainError('INVALID_EXTENSION_RESULT','feedback必须为1至8000字符的玩家反馈')
    updates=result.get('updates',[]);messages=result.get('messages',[])
    if not isinstance(updates,list) or len(updates)>20 or not isinstance(messages,list) or len(messages)>20:
        raise DomainError('INVALID_EXTENSION_RESULT','每次最多20条记录修改和20条消息')
    grants={g['type_key']:g for g in config.get('writes',[])}
    changed=set()
    from .workflows import ensure_unlocked,invalidate_dependents
    from .references import validate_references
    for update in updates:
        if not isinstance(update,dict) or set(update)!={'id','changes'} or not isinstance(update['id'],str):
            raise DomainError('INVALID_EXTENSION_RESULT','修改需要id与changes')
        row=by_id.get(update['id']);changes=update['changes']
        if row is None or row.id in changed or not isinstance(changes,dict) or not changes:
            raise DomainError('EXTENSION_DENIED','修改目标未授权或重复',403)
        grant=grants.get(row.type_key);type_spec=game.definition['types'][row.type_key]
        owner=type_spec.get('player_access',{}).get('owner_field')
        if (not grant or set(changes)-set(grant['fields']) or owner in changes
            or (grant['scope']=='own' and row.data.get(owner)!=actor)):
            raise DomainError('EXTENSION_DENIED','修改超出管理员授予的类型、归属或字段范围',403)
        # Even an admin's manual decision must respect existing waits belonging to other actions.
        ensure_unlocked(db,game.id,row.id,actor,run_id)
        before={'data':deepcopy(row.data),'version':row.version}
        updated={**row.data,**deepcopy(changes)}
        schema_validate(type_spec.get('schema',{'type':'object'}),updated)
        validate_references(db,game,type_spec.get('schema',{}),updated)
        row.data=updated;row.version+=1;changed.add(row.id)
        invalidate_dependents(service,db,game.id,row.id,run_id,actor)
        revision=db.get(EntityRevision,row.id)
        if revision:revision.last_version=row.version
        else:db.add(EntityRevision(id=row.id,last_version=row.version))
        service.event(db,game.id,actor,'extension.effect',{'id':row.id,'before':before,'after':{'data':updated,'version':row.version}})
    recipients={actor} if actor!='admin' else set()
    for row in rows:
        owner=game.definition['types'][row.type_key].get('player_access',{}).get('owner_field')
        if owner and isinstance(row.data.get(owner),str):recipients.add(row.data[owner])
    if messages and not config.get('messages',False):
        raise DomainError('EXTENSION_DENIED','处理器未获消息投递授权',403)
    from .policy import check_participant
    for message in messages:
        if (not isinstance(message,dict) or set(message)!={'recipient','text'}
            or not isinstance(message['recipient'],str) or message['recipient'] not in recipients
            or not re.fullmatch(r'qq:[0-9]{1,25}',message['recipient'])
            or not isinstance(message['text'],str) or not 1<=len(message['text'])<=8000):
            raise DomainError('EXTENSION_DENIED','消息受众或内容无效',403)
        recipient=message['recipient'];check_participant(db,game.id,recipient)
        target='private-'+recipient.removeprefix('qq:');channel=db.get(Channel,target)
        if not channel or channel.game_id!=game.id:
            raise DomainError('DELIVERY_UNBOUND','消息接收人尚未绑定本游戏私聊',409)
        db.add(Outbox(game_id=game.id,group_id=target,text=message['text']))
        service.event(db,game.id,actor,'extension.delivery',{'recipient':recipient,'characters':len(message['text'])})
    return feedback
    db.flush()
