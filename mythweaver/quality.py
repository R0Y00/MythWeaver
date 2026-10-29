"""Versioned source requirements and reproducible business acceptance evidence."""
from copy import deepcopy
import hashlib,json
from typing import Literal
from pydantic import Field
from sqlalchemy import select
from .schemas import Input
from .db import GameDocument,AcceptanceRun,PluginRelease
from .definitions import DomainError
from .service import packed
from .playtest import ChatStep,simulate_conversation


class Implementation(Input):
    section:Literal['types','interactions','workflows','functions','modules','plugins','policy']
    key:str=Field(min_length=1,max_length=80)


class Clause(Input):
    key:str=Field(pattern=r'^[a-z][a-z0-9_]{0,79}$')
    text:str=Field(min_length=1)
    status:Literal['planned','implemented','manual','deferred','out_of_scope']='planned'
    implementations:list[Implementation]=Field(default_factory=list)
    scenarios:list[str]=Field(default_factory=list)
    note:str=''


class Scenario(Input):
    key:str=Field(pattern=r'^[a-z][a-z0-9_]{0,79}$')
    label:str=Field(min_length=1,max_length=120)
    actor:str=Field(pattern=r'^qq:[0-9]{1,25}$')
    audience:Literal['private','group']='private'
    messages:list[ChatStep]=Field(min_length=1)


class DocumentPut(Input):
    key:str=Field(pattern=r'^[a-z][a-z0-9_]{0,79}$')
    revision:int=Field(ge=0)
    title:str=Field(min_length=1,max_length=120)
    source:str=Field(min_length=1,description='完整原文，不以摘要代替')
    clauses:list[Clause]=Field(min_length=1)
    scenarios:list[Scenario]=Field(default_factory=list)


class QualityQuery(Input):
    key:str|None=None
    revision:int|None=Field(default=None,ge=1)
    source_offset:int=Field(default=0,ge=0)
    source_limit:int=Field(default=6000,ge=1,le=10000)
    item_offset:int=Field(default=0,ge=0)
    item_limit:int=Field(default=5,ge=1,le=10)


class AcceptanceRequest(Input):
    key:str
    revision:int=Field(ge=1)
    allow_external:bool=False


def config_stamp(db,game):
    from .policy import policy_for
    from .platform_rules import read
    from .db import Participant,Channel,ModelProvider,PlatformConfig
    routing=db.get(PlatformConfig,'model_routing')
    value=[game.version,policy_for(db,game.id),read(db),
        [(p.id,p.digest,p.enabled,p.status) for p in db.scalars(select(PluginRelease).where(PluginRelease.game_id==game.id).order_by(PluginRelease.id))],
        [(p.actor,p.version,p.blocked) for p in db.scalars(select(Participant).where(Participant.game_id==game.id).order_by(Participant.actor))],
        list(db.scalars(select(Channel.group_id).where(Channel.game_id==game.id).order_by(Channel.group_id))),
        [(p.id,p.version,p.enabled) for p in db.scalars(select(ModelProvider).order_by(ModelProvider.id))],
        [routing.version,routing.value] if routing else None]
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def latest(db,game_id,key):
    return db.scalar(select(GameDocument).where(GameDocument.game_id==game_id,GameDocument.key==key).order_by(GameDocument.revision.desc()).limit(1))


def put(service,db,game,payload):
    args=DocumentPut.model_validate(payload);old=latest(db,game.id,args.key)
    if (old.revision if old else 0)!=args.revision:raise DomainError('STATE_STALE','规则文档已更新，请先重新读取',409)
    clause_keys=[c.key for c in args.clauses];case_keys=[s.key for s in args.scenarios]
    if len(set(clause_keys))!=len(clause_keys) or len(set(case_keys))!=len(case_keys):raise DomainError('INVALID_COVERAGE','条款或用例标识重复')
    for clause in args.clauses:
        if clause.text not in args.source:raise DomainError('INVALID_COVERAGE','每条条款须引用原文中的实际片段，不能伪造原文')
        if set(clause.scenarios)-set(case_keys):raise DomainError('INVALID_COVERAGE','条款引用了不存在的验收用例')
        if clause.status in ('implemented','manual') and not clause.implementations:
            raise DomainError('INVALID_COVERAGE','已实现或人工处理条款必须关联具体配置')
        if clause.status in ('deferred','out_of_scope') and not clause.note.strip():raise DomainError('INVALID_COVERAGE','暂缓或排除条款须保存原因')
    row=GameDocument(game_id=game.id,key=args.key,revision=args.revision+1,content=args.model_dump(exclude={'key','revision'}))
    db.add(row);db.flush();service.event(db,game.id,'admin','requirements.updated',{'id':row.id,'key':row.key,'revision':row.revision})
    return {'id':row.id,'key':row.key,'revision':row.revision,'source_chars':len(args.source),'clause_count':len(args.clauses),'scenario_count':len(args.scenarios)}


def tool_read(service,game_id,arguments):
    args=QualityQuery.model_validate(arguments)
    value=read(service,game_id,args.key,args.revision)
    if not args.key:
        return {'documents':[{'id':d['id'],'key':d['key'],'revision':d['revision'],'title':d['content']['title'],
            'source_chars':len(d['content']['source']),'clauses':len(d['coverage']),'ready':d['acceptance']['ready'],
            'unverified':d['acceptance']['unverified'],'unmapped_fragments':len(d['unmapped_source'])} for d in value['documents']]}
    value=deepcopy(value);source=value['content']['source']
    value['content']['source']=source[args.source_offset:args.source_offset+args.source_limit]
    value['source_page']={'total_chars':len(source),'offset':args.source_offset,'next_offset':args.source_offset+args.source_limit if args.source_offset+args.source_limit<len(source) else None}
    for key in ('clauses','scenarios'):
        items=value['content'][key];value['content'][key]=items[args.item_offset:args.item_offset+args.item_limit]
        value[key+'_page']={'total':len(items),'offset':args.item_offset,'next_offset':args.item_offset+args.item_limit if args.item_offset+args.item_limit<len(items) else None}
    value['coverage']=value['coverage'][args.item_offset:args.item_offset+args.item_limit]
    value['acceptance'].pop('scenarios',None)
    value['unmapped_source']=value['unmapped_source'][args.item_offset:args.item_offset+args.item_limit]
    return value


def reference_exists(db,game,reference):
    section,key=reference['section'],reference['key']
    if section=='plugins':return db.scalar(select(PluginRelease.id).where(PluginRelease.game_id==game.id,PluginRelease.key==key,PluginRelease.enabled==True,PluginRelease.status=='ready').limit(1)) is not None
    if section=='policy':
        from .policy import policy_for
        return key in policy_for(db,game.id)[0]
    return key in game.definition.get(section,{}) and game.definition[section][key].get('enabled',True)


def report(db,game,document):
    evidence=db.scalar(select(AcceptanceRun).where(AcceptanceRun.game_id==game.id,AcceptanceRun.document_id==document.id).order_by(AcceptanceRun.created_at.desc()).limit(1))
    current=bool(evidence and evidence.config_stamp==config_stamp(db,game) and latest(db,game.id,document.key).id==document.id)
    cases=evidence.results.get('scenarios',{}) if current else {}
    clauses=[]
    for raw in document.content['clauses']:
        item=deepcopy(raw)
        linked=bool(item['implementations']) and all(reference_exists(db,game,r) for r in item['implementations'])
        verified=item['status'] in ('implemented','manual') and linked and bool(item['scenarios']) and all(cases.get(k,{}).get('passed') is True for k in item['scenarios'])
        item.update(linked=linked,verified=verified)
        clauses.append(item)
    included=[c for c in clauses if c['status']!='out_of_scope']
    untested=[c['key'] for c in included if not c['verified']]
    source=document.content['source'];covered=[False]*len(source)
    for clause in clauses:
        offset=0
        while True:
            index=source.find(clause['text'],offset)
            if index<0:break
            covered[index:index+len(clause['text'])]=[True]*len(clause['text']);offset=index+len(clause['text'])
    unmapped=''.join(c if not covered[i] else '\n' for i,c in enumerate(source))
    gaps=[part.strip() for part in unmapped.splitlines() if part.strip()]
    versions=[{'id':r.id,'revision':r.revision,'created_at':r.created_at.isoformat()} for r in db.scalars(select(GameDocument).where(GameDocument.game_id==game.id,GameDocument.key==document.key).order_by(GameDocument.revision.desc()))]
    return {**packed(document),'coverage':clauses,'revisions':versions,'unmapped_source':gaps,'acceptance':{'current':current,'run_id':evidence.id if evidence else None,
        'ready':bool(included) and not untested and not gaps,'unverified':untested,'scenarios':cases},
        'notice':'就绪要求完整原文均有条款映射、实际配置存在、全部范围内条款有当前版本的通过用例。语义正确性仍需GM审查，不以模型自述作为证据。'}


def read(service,game_id,key=None,revision=None):
    with service.store.session() as db:
        game=service.game(db,game_id)
        if key:
            row=db.scalar(select(GameDocument).where(GameDocument.game_id==game_id,GameDocument.key==key,GameDocument.revision==revision)) if revision else latest(db,game_id,key)
            if not row:raise DomainError('NOT_FOUND','规则文档不存在',404)
            return report(db,game,row)
        seen=set();rows=[]
        for row in db.scalars(select(GameDocument).where(GameDocument.game_id==game_id).order_by(GameDocument.key,GameDocument.revision.desc())):
            if row.key in seen:continue
            seen.add(row.key);rows.append(report(db,game,row))
        return {'documents':rows}


async def run_acceptance(service,settings,game_id,arguments):
    args=AcceptanceRequest.model_validate(arguments)
    with service.store.session() as db:
        game=service.game(db,game_id);document=latest(db,game_id,args.key)
        if not document or document.revision!=args.revision:raise DomainError('STATE_STALE','规则文档版本已变化',409)
        stamp=config_stamp(db,game);document_id=document.id;scenarios=deepcopy(document.content['scenarios'])
    results={}
    for scenario in scenarios:
        results[scenario['key']]=await simulate_conversation(service,settings,game_id,{k:scenario[k] for k in ('actor','audience','messages')}|{'allow_external':args.allow_external})
    with service.store.session.begin() as db:
        game=service.game(db,game_id,lock=True)
        if config_stamp(db,game)!=stamp or latest(db,game_id,args.key).id!=document_id:
            raise DomainError('STATE_STALE','测试期间规则或文档变化，本次不能作为验收',409)
        row=AcceptanceRun(game_id=game_id,document_id=document_id,config_stamp=stamp,results={'scenarios':results})
        db.add(row);db.flush()
        value=report(db,game,latest(db,game_id,args.key))
        service.event(db,game_id,'admin','acceptance.completed',{'id':row.id,'ready':value['acceptance']['ready']})
        return value
