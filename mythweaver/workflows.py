"""Optional persistent, bounded flows; no genre-specific concepts or code execution."""
from copy import deepcopy
import re

from sqlalchemy import select

from .conditions import evaluate, required_path, validate_condition
from .db import Entity, FlowRun, utcnow
from .definitions import DomainError, check_schema, schema_validate


def validate_workflows(definition):
    from jsonschema import Draft202012Validator
    from .rules import check_effects
    flows=definition.get('workflows',{})
    if not isinstance(flows,dict) or len(flows)>30:
        raise DomainError('INVALID_FLOW','最多定义 30 个流程')
    for key,flow in flows.items():
        if not isinstance(key,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}',key) or not isinstance(flow,dict):
            raise DomainError('INVALID_FLOW','流程标识或结构无效')
        if set(flow)-{'label','enabled','schema','start','steps'} or not isinstance(flow.get('enabled',True),bool) or not isinstance(flow.get('label',key),str) or len(flow.get('label',key))>120:
            raise DomainError('INVALID_FLOW','流程配置字段无效')
        steps=flow.get('steps',{})
        if not isinstance(steps,dict) or not 1<=len(steps)<=40 or flow.get('start') not in steps:
            raise DomainError('INVALID_FLOW','流程需要起始步骤及 1–40 个步骤')
        schemas=[flow.get('schema',{'type':'object'})]
        edges={}
        for step_key,step in steps.items():
            if not isinstance(step_key,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}',step_key) or not isinstance(step,dict):
                raise DomainError('INVALID_FLOW','步骤标识或结构无效')
            kind=step.get('kind')
            allowed={'kind','label'}|({'next','effects','extension'} if kind=='effects' else {'schema','next','locks','effects','extension'} if kind=='manual' else {'condition','yes','no'} if kind=='branch' else {'result'} if kind=='end' else set())
            if kind not in ('effects','manual','branch','end') or set(step)-allowed or not isinstance(step.get('label',step_key),str) or len(step.get('label',step_key))>200:
                raise DomainError('INVALID_FLOW','步骤类型或配置无效')
            check_effects(definition,step)
            from .extensions import validate
            validate(definition,step)
            if kind=='manual':
                schemas.append(step.get('schema',{'type':'object'}))
                locks=step.get('locks',[])
                if not isinstance(locks,list) or len(locks)>20 or any(not isinstance(p,str) or not p or len(p)>240 for p in locks):
                    raise DomainError('INVALID_FLOW','等待范围必须是最多 20 个输入路径')
            if kind=='end' and (not isinstance(step.get('result',''),str) or len(step.get('result',''))>4000):
                raise DomainError('INVALID_FLOW','结束说明过长')
            if kind=='branch':
                validate_condition(step.get('condition'),definition)
            edges[step_key]=[step.get(k) for k in (('yes','no') if kind=='branch' else ('next',) if kind!='end' else ())]
            if any(not isinstance(target,str) or target not in steps for target in edges[step_key]):
                raise DomainError('INVALID_FLOW','后续步骤不存在')
        for schema in schemas:
            if not isinstance(schema,dict) or schema.get('type')!='object':
                raise DomainError('INVALID_FLOW','流程输入与人工决定必须为对象结构')
            check_schema(schema)
            from .references import check_reference_schema
            check_reference_schema(schema,definition)
            try:
                Draft202012Validator.check_schema(schema)
            except Exception:
                raise DomainError('INVALID_FLOW','流程字段结构无效') from None
        visiting,visited=set(),set()
        def visit(node):
            if node in visiting:
                raise DomainError('INVALID_FLOW','当前流程不允许循环，请显式启动下一次流程')
            if node in visited:
                return
            visiting.add(node)
            for child in edges[node]:
                visit(child)
            visiting.remove(node)
            visited.add(node)
        for node in steps:
            visit(node)
    for spec in definition.get('interactions',{}).values():
        if spec.get('mode')=='flow':
            if spec.get('workflow') not in flows or spec.get('effects'):
                raise DomainError('INVALID_FLOW','流程交互需引用已定义流程，数据变更应在流程步骤中配置')


def ensure_unlocked(db,game_id,entity_id,actor,run_id=None):
    if actor=='admin':
        return
    for run in db.scalars(select(FlowRun).where(FlowRun.game_id==game_id,FlowRun.status=='waiting')):
        if run.id!=run_id and entity_id in run.locks:
            raise DomainError('OBJECT_WAITING','该记录正在等待管理员决定，其他记录仍可操作',409)


def invalidate_dependents(service,db,game_id,entity_id,run_id=None,actor="admin"):
    for run in db.scalars(select(FlowRun).where(FlowRun.game_id==game_id,FlowRun.status=='waiting')):
        if run.id!=run_id and entity_id in run.dependencies:
            run.status,run.locks='stale',[]
            run.version+=1
            service.event(db,game_id,actor,'flow.stale',{'id':run.id,'reason':'目标记录已修改','entity_id':entity_id})


def check_dependencies(db,game,dependencies):
    for entity_id,version in dependencies.items():
        row=db.get(Entity,entity_id)
        if not row or row.game_id!=game.id or row.version!=version or row.archived:
            raise DomainError('STATE_STALE','流程依赖数据已变化，请取消后重新发起',409)


def note(run,step,kind,detail=None):
    run.history=[*run.history,{'step':step,'kind':kind,'at':utcnow().isoformat(),**(detail or {})}]


def effects(service,db,game,run,step,actor=None):
    from .rules import apply_effects,targets
    dependencies=dict(run.dependencies)
    targets_before=targets(db,game,step,run.context,run.actor)
    apply_effects(service,db,game,actor or run.actor,step,run.context,run_id=run.id,extension_actor=run.actor)
    for entity_id in targets_before:
        dependencies[entity_id]=db.get(Entity,entity_id).version
    run.dependencies=dependencies


def advance(service,db,game,run,actor=None):
    actor=actor or run.actor
    flow=game.definition['workflows'][run.workflow]
    for _ in range(40):
        key=run.current_step
        step=flow['steps'][key]
        kind=step['kind']
        if kind=='end':
            run.status,run.locks,run.result='completed',[],step.get('result','')
            note(run,key,'completed')
            service.event(db,game.id,actor,'flow.completed',{'id':run.id,'step':key})
            return
        if kind=='manual':
            locks=[]
            dependencies=dict(run.dependencies)
            from .extensions import dependencies as extension_dependencies
            dependencies.update(extension_dependencies(db,game,step,run.actor))
            for path in step.get('locks',[]):
                entity_id=required_path(run.context,path)
                row=db.get(Entity,entity_id) if isinstance(entity_id,str) else None
                if not row or row.game_id!=game.id or row.archived:
                    raise DomainError('INVALID_TARGET','等待范围的目标记录不可用')
                from .player import authorize_target
                authorize_target(db,game,row,actor)
                ensure_unlocked(db,game.id,row.id,actor,run.id)
                locks.append(row.id)
                dependencies[row.id]=row.version
            run.status,run.locks,run.dependencies='waiting',locks,dependencies
            note(run,key,'waiting')
            service.event(db,game.id,actor,'flow.waiting',{'id':run.id,'step':key,'locks':locks})
            return
        if kind=='branch':
            dependencies=dict(run.dependencies)
            matched=evaluate(db,game,step['condition'],run.context,dependencies)
            run.dependencies=dependencies
            note(run,key,'branch',{'matched':matched})
            run.current_step=step['yes'] if matched else step['no']
        else:
            effects(service,db,game,run,step,actor)
            note(run,key,'effects',{'count':len(step.get('effects',[]))})
            run.current_step=step['next']
    raise DomainError('FLOW_LIMIT','流程超过单次步骤上限')


def start(service,db,game,actor,p,submission_id=None):
    workflow=p['workflow']
    flow=game.definition.get('workflows',{}).get(workflow)
    if not flow or not flow.get('enabled',True):
        raise DomainError('FLOW_UNAVAILABLE','流程不存在或已停用')
    schema_validate(flow.get('schema',{'type':'object'}),p['data'])
    from .references import validate_references
    validate_references(db,game,flow.get('schema',{}),p['data'])
    run=FlowRun(game_id=game.id,workflow=workflow,actor=actor,submission_id=submission_id,
                current_step=flow['start'],context={'input':deepcopy(p['data']),'answers':{}},definition_version=game.version)
    db.add(run)
    db.flush()
    service.event(db,game.id,actor,'flow.started',{'id':run.id,'workflow':workflow,'submission_id':submission_id})
    advance(service,db,game,run,actor)
    db.flush()
    from .service import packed
    return packed(run)


def change(service,db,game,p,cancel=False):
    if not cancel and p.get('fingerprint'):
        from .agent import fingerprint
        if fingerprint(db,game)!=p['fingerprint']:
            raise DomainError('STATE_STALE','预演之后游戏状态已变化，请重新预览',409)
    run=db.get(FlowRun,p['id'])
    if not run or run.game_id!=game.id:
        raise DomainError('NOT_FOUND','流程实例不存在',404)
    if run.version!=p['version']:
        raise DomainError('STATE_STALE','流程已变化，请刷新',409)
    if cancel:
        if run.status not in ('waiting','stale'):
            raise DomainError('STATE_STALE','流程已经结束',409)
        run.status,run.locks='cancelled',[]
        run.version+=1
        note(run,run.current_step,'cancelled',{'reason':p['reason']})
        service.event(db,game.id,'admin','flow.cancelled',{'id':run.id,'reason':p['reason']})
    else:
        if run.status!='waiting' or run.definition_version!=game.version:
            raise DomainError('STATE_STALE','流程不再等待决定或定义已变化',409)
        flow=game.definition.get('workflows',{}).get(run.workflow)
        if not flow or not flow.get('enabled',True):
            raise DomainError('FLOW_UNAVAILABLE','流程已停用')
        step=flow['steps'][run.current_step]
        check_dependencies(db,game,run.dependencies)
        from .extensions import dependencies as extension_dependencies
        if any(run.dependencies.get(k)!=v for k,v in extension_dependencies(db,game,step,run.actor).items()):
            raise DomainError('STATE_STALE','扩展读取范围已变化，请重新发起流程',409)
        schema_validate(step.get('schema',{'type':'object'}),p['data'])
        from .references import validate_references
        validate_references(db,game,step.get('schema',{}),p['data'])
        run.context={**run.context,'answers':{**run.context.get('answers',{}),run.current_step:deepcopy(p['data'])}}
        effects(service,db,game,run,step,'admin')
        note(run,run.current_step,'decided',{'data':p['data'],'reason':p['reason']})
        service.event(db,game.id,'admin','flow.decided',{'id':run.id,'step':run.current_step,'data':p['data'],'reason':p['reason']})
        run.current_step=step['next']
        run.status,run.locks='running',[]
        run.version+=1
        advance(service,db,game,run,'admin')
    db.flush()
    from .service import packed
    return packed(run)


def preview_resume(service,game_id,payload):
    from uuid import uuid4
    from sqlalchemy import func
    from .db import Event
    from .agent import fingerprint
    from .service import packed
    db=service.store.session()
    try:
        db.begin()
        game=service.game(db,game_id,lock=True)
        state=fingerprint(db,game)
        last=db.scalar(select(func.max(Event.seq)).where(Event.game_id==game_id)) or 0
        result=service.execute(db,game_id,'admin','flow-preview:'+uuid4().hex,'flow.resume',payload)
        db.flush()
        changes=[packed(e) for e in db.scalars(select(Event).where(Event.game_id==game_id,Event.seq>last).order_by(Event.seq))]
        return {'run':result,'events':changes,'fingerprint':state}
    finally:
        db.rollback()
        db.close()
