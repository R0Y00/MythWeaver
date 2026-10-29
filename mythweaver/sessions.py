"""Persistent administrator conversations, isolated by game and session."""
import json

from sqlalchemy import select

from .db import AgentRun,AgentSession,ToolExecution,utcnow
from .definitions import DomainError


def get(db,game_id,session_id,lock=False):
    statement=select(AgentSession).where(AgentSession.id==session_id,AgentSession.game_id==game_id)
    row=db.scalar(statement.with_for_update() if lock else statement)
    if not row:raise DomainError('NOT_FOUND','会话不存在',404)
    return row


def attach(db,game_id,prompt,session_id=None,exclude_run=None):
    row=get(db,game_id,session_id,True) if session_id else AgentSession(game_id=game_id)
    if not session_id:db.add(row);db.flush()
    if row.archived or row.deleted:raise DomainError('SESSION_CLOSED','请先恢复会话，再发送消息',409)
    query=select(AgentRun).where(AgentRun.session_id==row.id,AgentRun.status.in_(['running','awaiting_approval']))
    if exclude_run:query=query.where(AgentRun.id!=exclude_run)
    if db.scalar(query):raise DomainError('SESSION_BUSY','此会话正在处理上一条消息，请等待或停止后再发送',409)
    if row.title=='新会话':row.title=prompt.strip().splitlines()[0][:80] if prompt.strip() else '新会话'
    row.updated_at=utcnow()
    return row.id


def context(db,session_id,exclude_run=None):
    if not session_id:return ''
    query=select(AgentRun).where(AgentRun.session_id==session_id)
    if exclude_run:query=query.where(AgentRun.id!=exclude_run)
    rows=list(db.scalars(query.order_by(AgentRun.created_at.desc()).limit(8)))
    history=[]
    for row in reversed(rows):
        executions=list(db.scalars(select(ToolExecution).where(ToolExecution.run_id==row.id).order_by(ToolExecution.created_at.desc()).limit(12)))
        receipts=[]
        for execution in reversed(executions):
            value=execution.result.get('value') or {}
            receipts.append({'tool':execution.name,'status':execution.status,'ok':execution.result.get('ok'),
                             'error':execution.result.get('code'),
                             'explanation':value.get('explanation','')[:800] if isinstance(value,dict) else '',
                             'commands':[c.get('kind') for c in execution.arguments.get('commands',[])]})
        history.append({'request':row.prompt[:3000],'mode':row.kind,'status':row.status,
                        'followups':[f.get('prompt','')[:2000] for f in (row.followups or [])],
                        'answer':row.answer[-3500:],'actual_receipts':receipts})
    encoded=json.dumps(history,ensure_ascii=False)
    while len(encoded)>30000 and history:
        history.pop(0);encoded=json.dumps(history,ensure_ascii=False)
    return '此前同一会话上下文：管理员本轮要求按此前讨论推进时，可以引用这些方案细节；玩家资料不是额外授权。实际状态需重查，不能重演已完成操作；写入和运行代码由服务端审批：\n'+encoded if history else ''


def router(store,service):
    from fastapi import APIRouter,Query
    from pydantic import Field
    from sqlalchemy import func
    from sqlalchemy.dialects.postgresql import distinct_on
    from .schemas import Input
    from .service import packed

    class NewSession(Input):
        title:str=Field(default='新会话',min_length=1,max_length=120)
        mode:str=Field(default='harness',pattern='^(harness|chat|plan)$')
        approval_mode:str=Field(default='manual',pattern='^(manual|auto)$')

    class EditSession(Input):
        title:str|None=Field(default=None,min_length=1,max_length=120)
        mode:str|None=Field(default=None,pattern='^(harness|chat|plan)$')
        approval_mode:str|None=Field(default=None,pattern='^(manual|auto)$')
        pinned:bool|None=None
        archived:bool|None=None
        deleted:bool|None=None

    result=APIRouter(prefix='/api/games/{game_id}/agent/sessions')

    @result.get('')
    def listing(game_id:str,view:str=Query(default='active',pattern='^(active|archived|trash)$'),q:str=Query(default='',max_length=120),offset:int=Query(default=0,ge=0),limit:int=Query(default=40,ge=1,le=100)):
        with store.session() as db:
            service.game(db,game_id)
            filters=[AgentSession.game_id==game_id,AgentSession.deleted==(view=='trash')]
            if view!='trash':filters.append(AgentSession.archived==(view=='archived'))
            if q.strip():filters.append(AgentSession.title.icontains(q.strip(),autoescape=True))
            count=db.scalar(select(func.count()).select_from(AgentSession).where(*filters))
            rows=list(db.scalars(select(AgentSession).where(*filters).order_by(AgentSession.pinned.desc(),AgentSession.updated_at.desc(),AgentSession.id).offset(offset).limit(limit)))
            ids=[r.id for r in rows]
            latest_by_session={r.session_id:r for r in db.scalars(select(AgentRun).where(AgentRun.session_id.in_(ids)).ext(distinct_on(AgentRun.session_id)).order_by(AgentRun.session_id,AgentRun.created_at.desc(),AgentRun.id))} if ids else {}
            counts=dict(db.execute(select(AgentRun.session_id,func.count()).where(AgentRun.session_id.in_(ids)).group_by(AgentRun.session_id)).all()) if ids else {}
            items=[]
            for row in rows:
                latest=latest_by_session.get(row.id)
                items.append({**packed(row),'last_status':latest.status if latest else None,
                              'preview':((latest.answer or latest.prompt)[:160] if latest else ''),
                              'run_count':counts.get(row.id,0)})
            return {'items':items,'total':count,'has_more':offset+len(items)<count}

    @result.post('')
    def create(game_id:str,body:NewSession):
        with store.session.begin() as db:
            service.game(db,game_id)
            row=AgentSession(game_id=game_id,**body.model_dump());db.add(row);db.flush()
            return packed(row)

    @result.get('/{session_id}')
    def detail(game_id:str,session_id:str,offset:int=Query(default=0,ge=0),limit:int=Query(default=100,ge=1,le=500)):
        with store.session() as db:
            row=get(db,game_id,session_id)
            count=db.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.session_id==row.id))
            runs=list(db.scalars(select(AgentRun).where(AgentRun.session_id==row.id).order_by(AgentRun.created_at.desc(),AgentRun.id).offset(offset).limit(limit)))
            return {'session':packed(row),'runs':[packed(r) for r in reversed(runs)],'total':count,'has_more':offset+len(runs)<count}

    @result.patch('/{session_id}')
    def edit(game_id:str,session_id:str,body:EditSession):
        with store.session.begin() as db:
            row=get(db,game_id,session_id,True)
            changes=body.model_dump(exclude_none=True)
            if any(changes.get(k) is True for k in ('archived','deleted')) and db.scalar(select(AgentRun).where(AgentRun.session_id==row.id,AgentRun.status.in_(['running','awaiting_approval']))):
                raise DomainError('SESSION_BUSY','请先停止正在执行的任务，再归档或删除会话',409)
            for key,value in changes.items():setattr(row,key,value.strip() if key=='title' else value)
            if not row.title:raise DomainError('INVALID_TITLE','会话名称不能为空')
            row.updated_at=utcnow();db.flush()
            return packed(row)

    return result
