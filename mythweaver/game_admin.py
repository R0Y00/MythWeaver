"""Transactional game administration shared by UI and Agent commands."""
from copy import deepcopy
import re

from sqlalchemy import select,update

from .db import Blueprint,Channel,Entity,EntityRevision,FlowRun,Game,GamePolicy,Inbox,Outbox,Participant,RetiredEntity,Submission
from .definitions import DomainError,schema_validate,validate_definition
from .policy import policy_for
from .service import packed

KINDS={'game.create','policy.update','participant.update','channel.bind','channel.unbind','checkpoint.create','checkpoint.restore',
       'blueprint.save','blueprint.create','blueprint.instantiate','game.clone','definition.migrate','entity.retire','entity.restore','flow.redirect','submission.revise',
       'module.put','module.state','module.remove'}
KINDS.add('requirements.put')


def bump(db,entity_id,version):
    ledger=db.get(EntityRevision,entity_id)
    if ledger:
        ledger.last_version=max(ledger.last_version,version)
    else:
        db.add(EntityRevision(id=entity_id,last_version=version))


def validate_records(db,game):
    from .references import validate_references
    for row in db.scalars(select(Entity).where(Entity.game_id==game.id)):
        spec=game.definition.get('types',{}).get(row.type_key)
        if not spec:
            raise DomainError('MIGRATION_REQUIRED','仍有记录使用要删除的类型，请迁移或退役这些记录',409)
        schema_validate(spec.get('schema',{'type':'object'}),row.data)
        validate_references(db,game,spec.get('schema',{}),row.data,historical=row.archived)


def retire(service,db,game,p,validate=True):
    row=db.get(Entity,p['id'])
    if not row or row.game_id!=game.id:
        raise DomainError('NOT_FOUND','记录不存在',404)
    if row.version!=p['version']:
        raise DomainError('STATE_STALE','记录已变化',409)
    before=packed(row)
    from .workflows import invalidate_dependents
    invalidate_dependents(service,db,game.id,row.id)
    tombstone=RetiredEntity(id=row.id,game_id=game.id,snapshot=before,version=row.version+1,reason=p['reason'])
    db.add(tombstone)
    bump(db,row.id,tombstone.version)
    db.delete(row)
    db.flush()
    if validate:
        validate_records(db,game)
    service.event(db,game.id,'admin','entity.retired',{'before':before,'version':tombstone.version,'reason':p['reason']})
    return packed(tombstone)


def handle(service,db,game,kind,p):
    if kind=='requirements.put':
        from .quality import put
        return put(service,db,game,p)
    if kind.startswith('module.'):
        from .modules import change
        return change(service,db,game,kind,p)
    if kind=='game.create':
        return create_game_in(service,db,p['name'],p['description'])
    if kind=='policy.update':
        row=db.get(GamePolicy,game.id)
        if (row.version if row else 0)!=p['version']:
            raise DomainError('STATE_STALE','游戏设置已变化',409)
        before=policy_for(db,game.id)
        value={**(row.config if row else {}),**{k:v for k,v in p.items() if k!='version'}}
        merged={**before[0],**{k:v for k,v in value.items() if v is not None}}
        if merged['free_mode'] not in ('manual','record') or any(not re.fullmatch(r'qq:[0-9]{1,25}',u) for u in merged['allow_users']):
            raise DomainError('INVALID_CONFIG','自由输入方式或参与者标识无效')
        if row:
            row.config=value
            row.version+=1
        else:
            row=GamePolicy(game_id=game.id,config=value,version=1)
            db.add(row)
        service.event(db,game.id,'admin','policy.updated',{'before':{'config':before[0],'version':before[1]},'after':{**value,'version':row.version}})
        db.flush()
        return {**policy_for(db,game.id)[0],'version':row.version}
    if kind=='participant.update':
        row=db.scalar(select(Participant).where(Participant.game_id==game.id,Participant.actor==p['actor']))
        if p.get('version') is not None and p['version']!=(row.version if row else 0):
            raise DomainError('STATE_STALE','参与者已变化',409)
        before=packed(row) if row else None
        if not row:
            row=Participant(game_id=game.id,actor=p['actor'],version=1)
            db.add(row)
        else:
            row.version+=1
        row.label,row.blocked=p['label'],p['blocked']
        db.flush()
        service.event(db,game.id,'admin','participant.updated',{'before':before,'after':packed(row)})
        return packed(row)
    if kind in ('channel.bind','channel.unbind'):
        row=db.get(Channel,p['group_id'])
        if row and row.game_id!=game.id:
            raise DomainError('ALREADY_BOUND','该群属于其他游戏，须在原游戏先解绑',409)
        if kind=='channel.bind' and not row:
            db.add(Channel(group_id=p['group_id'],game_id=game.id))
        if kind=='channel.unbind':
            if row:
                db.delete(row)
            db.execute(update(Outbox).where(Outbox.game_id==game.id,Outbox.group_id==p['group_id'],Outbox.status=='queued').values(status='cancelled'))
            for incoming in db.scalars(select(Inbox).where(Inbox.game_id==game.id,Inbox.status.in_(['queued','processing']))):
                if incoming.key.split(':')[1]==p['group_id']:
                    incoming.status='cancelled'
        service.event(db,game.id,'admin',kind,{'group_id':p['group_id']})
        return {'group_id':p['group_id'],'game_id':game.id,'bound':kind=='channel.bind','ok':True}
    if kind=='checkpoint.create':
        from .checkpoints import checkpoint_create_in
        return checkpoint_create_in(service,db,game,p['label'])
    if kind=='checkpoint.restore':
        from .checkpoints import checkpoint_restore_in
        return checkpoint_restore_in(service,db,game,p)
    if kind in ('blueprint.save','blueprint.create'):
        row=create_blueprint_in(db,p['name'],p['description'],game.definition if kind=='blueprint.save' else p['definition'])
        service.event(db,game.id,'admin',kind,{'id':row['id'],'name':row['name']})
        return row
    if kind=='blueprint.instantiate':
        row=instantiate_blueprint_in(service,db,p['id'],p['label'])
        service.event(db,game.id,'admin',kind,{'blueprint_id':p['id'],'created_game_id':row['id']})
        return row
    if kind=='game.clone':
        if not p['name'].strip():
            raise DomainError('INVALID_NAME','游戏名称不能为空')
        row=Game(name=p['name'].strip(),description=p['description'],definition=deepcopy(game.definition))
        db.add(row)
        db.flush()
        service.event(db,game.id,'admin',kind,{'created_game_id':row.id})
        service.event(db,row.id,'admin','game.created',{'source_game_id':game.id})
        return packed(row)
    if kind=='entity.retire':
        return retire(service,db,game,p)
    if kind=='entity.restore':
        from .references import validate_references
        saved=db.get(RetiredEntity,p['id'])
        if not saved or saved.game_id!=game.id:
            raise DomainError('NOT_FOUND','退役记录不存在',404)
        if saved.version!=p['version']:
            raise DomainError('STATE_STALE','退役记录已变化',409)
        snapshot=deepcopy(saved.snapshot)
        if p.get('type_key') is not None:
            snapshot['type_key']=p['type_key']
        if p.get('data') is not None:
            snapshot['data']=p['data']
        spec=game.definition.get('types',{}).get(snapshot['type_key'])
        if not spec or not spec.get('enabled',True):
            raise DomainError('MIGRATION_REQUIRED','请先恢复或迁移原类型定义',409)
        schema_validate(spec.get('schema',{'type':'object'}),snapshot['data'])
        validate_references(db,game,spec.get('schema',{}),snapshot['data'],historical=snapshot['archived'])
        ledger=db.get(EntityRevision,saved.id)
        new_version=max(saved.version,ledger.last_version if ledger else 0)+1
        restored=Entity(**{k:snapshot[k] for k in ('id','game_id','type_key','data','visibility','archived')},version=new_version)
        db.add(restored)
        bump(db,saved.id,new_version)
        db.delete(saved)
        db.flush()
        service.event(db,game.id,'admin','entity.restored',{'before':saved.snapshot,'after':packed(restored),'reason':p['reason']})
        return packed(restored)
    if kind=='definition.migrate':
        if game.version!=p['version']:
            raise DomainError('STATE_STALE','定义已变化，请重新规划迁移',409)
        ids=[c['id'] for c in p['changes']]+[c['id'] for c in p['retire']]
        if len(ids)!=len(set(ids)):
            raise DomainError('INVALID_MIGRATION','同一记录不能同时重复迁移或退役')
        definition=validate_definition(p['definition'])
        from .checkpoints import checkpoint_create_in
        safety=checkpoint_create_in(service,db,game,'迁移前自动保存 · '+p['reason'][:70])
        before=deepcopy(game.definition)
        for change in p['changes']:
            row=db.get(Entity,change['id'])
            if not row or row.game_id!=game.id:
                raise DomainError('NOT_FOUND','迁移记录不存在',404)
            if row.version!=change['version']:
                raise DomainError('STATE_STALE','迁移目标已变化',409)
            if change['visibility'] not in ('private','public'):
                raise DomainError('INVALID_VISIBILITY','无效的可见标记')
            old=packed(row)
            row.type_key,row.data,row.archived,row.visibility=change['type_key'],change['data'],change['archived'],change['visibility']
            row.version+=1
            bump(db,row.id,row.version)
            service.event(db,game.id,'admin','entity.migrated',{'before':old,'after':packed(row),'reason':p['reason']})
        for change in p['retire']:
            retire(service,db,game,change,validate=False)
        game.definition=definition
        db.flush()
        validate_records(db,game)
        game.version+=1
        service.stale_flows(db,game.id)
        for item in db.scalars(select(Submission).where(Submission.game_id==game.id,Submission.status=='pending')):
            item.status='stale'
            item.version+=1
            service.remember_submission_version(db,item)
        service.event(db,game.id,'admin',kind,{'before':before,'after':definition,'version':game.version,'reason':p['reason'],'safety_checkpoint':safety['id']})
        return {'version':game.version,'definition':definition,'changed':len(p['changes']),'retired':len(p['retire']),'safety_checkpoint':safety['id']}
    if kind=='flow.redirect':
        from .workflows import advance,note
        from .references import validate_references
        run=db.get(FlowRun,p['id'])
        if not run or run.game_id!=game.id:
            raise DomainError('NOT_FOUND','流程实例不存在',404)
        if run.version!=p['version']:
            raise DomainError('STATE_STALE','流程实例已变化',409)
        flow=game.definition.get('workflows',{}).get(run.workflow)
        if not flow or not flow.get('enabled',True) or p['step'] not in flow['steps']:
            raise DomainError('FLOW_UNAVAILABLE','请先定义并启用目标流程步骤')
        before=packed(run)
        context=deepcopy(run.context)
        if p['input'] is not None:
            context['input']=p['input']
        schema_validate(flow.get('schema',{'type':'object'}),context['input'])
        validate_references(db,game,flow.get('schema',{}),context['input'])
        for key,answer in p['answers'].items():
            step=flow['steps'].get(key)
            if not step or step['kind']!='manual':
                raise DomainError('INVALID_FLOW','只能为已定义的人工步骤指定回答')
            schema_validate(step.get('schema',{'type':'object'}),answer)
            validate_references(db,game,step.get('schema',{}),answer)
            context['answers'][key]=answer
        run.context,run.dependencies,run.locks=context,{},[]
        run.current_step,run.status,run.definition_version=p['step'],'running',game.version
        run.version+=1
        note(run,p['step'],'redirected',{'reason':p['reason']})
        advance(service,db,game,run,'admin')
        db.flush()
        service.event(db,game.id,'admin',kind,{'before':before,'after':packed(run),'reason':p['reason']})
        return packed(run)
    if kind=='submission.revise':
        from .references import validate_references
        row=db.get(Submission,p['id'])
        if not row or row.game_id!=game.id:
            raise DomainError('NOT_FOUND','参与事项不存在',404)
        if row.version!=p['version']:
            raise DomainError('STATE_STALE','参与事项已变化',409)
        if p['status'] not in ('pending','applied','rejected','cancelled'):
            raise DomainError('INVALID_STATE','修订状态无效')
        spec=game.definition.get('interactions',{}).get(p['interaction'])
        if p['interaction']=='_free':
            spec={'schema':{'type':'object','properties':{'text':{'type':'string','maxLength':8000}},'required':['text'],'additionalProperties':False}}
        if not spec:
            raise DomainError('INTERACTION_UNAVAILABLE','请先定义交互或改为自由文字')
        schema_validate(spec.get('schema',{'type':'object'}),p['data'])
        validate_references(db,game,spec.get('schema',{}),p['data'])
        if p['status']=='pending' and (spec.get('enabled') is False or spec.get('mode')=='flow'):
            raise DomainError('INVALID_STATE','停用或流程交互不能重新开启普通待决定事项')
        before=packed(row)
        row.interaction,row.data,row.status,row.result=p['interaction'],p['data'],p['status'],p['result']
        row.version+=1
        row.definition_version=game.version
        service.remember_submission_version(db,row)
        from .rules import targets
        row.dependencies=targets(db,game,spec,row.data,row.actor) if row.status=='pending' else {}
        if row.status=='pending' and 'condition' in spec:
            from .conditions import evaluate
            deps=dict(row.dependencies)
            if not evaluate(db,game,spec['condition'],row.data,deps):
                raise DomainError('PRECONDITION_FAILED','重新开启的事项不满足条件')
            row.dependencies=deps
        service.event(db,game.id,'admin',kind,{'before':before,'after':packed(row),'reason':p['reason'],'side_effects_replayed':False})
        return packed(row)
    raise DomainError('UNKNOWN_COMMAND','尚未实现的管理操作')


def create_game_in(service,db,name,description):
    if not name.strip():
        raise DomainError('INVALID_NAME','游戏名称不能为空')
    row=Game(name=name.strip(),description=description)
    db.add(row)
    db.flush()
    service.event(db,row.id,'admin','game.created',{'name':row.name})
    return packed(row)


def create_blueprint_in(db,name,description,definition):
    if not name.strip():
        raise DomainError('INVALID_NAME','模板名称不能为空')
    row=Blueprint(name=name.strip(),description=description,definition=validate_definition(deepcopy(definition)))
    db.add(row)
    db.flush()
    return packed(row)


def instantiate_blueprint_in(service,db,blueprint_id,label):
    if not label.strip():
        raise DomainError('INVALID_NAME','游戏名称不能为空')
    source=db.get(Blueprint,blueprint_id)
    if not source:
        raise DomainError('NOT_FOUND','模板不存在',404)
    row=Game(name=label.strip(),description=source.description,definition=validate_definition(deepcopy(source.definition)))
    db.add(row)
    db.flush()
    service.event(db,row.id,'admin','game.from_blueprint',{'blueprint_id':source.id})
    return packed(row)
