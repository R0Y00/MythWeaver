"""Keep durable tool receipts intact; project bounded, explicitly partial observations."""
import json
from pydantic import Field
from sqlalchemy import select

from .db import ToolExecution
from .definitions import DomainError
from .schemas import Input


class ResultRead(Input):
    call_id:str|None=Field(default=None,min_length=1,max_length=200,description='省略时分页列出本任务已完成回执目录；指定后读取该回执')
    path:str=Field(default='',description='已保存回执的JSON Pointer，例如/value/modules/fishing；空字符串为完整回执')
    offset:int=Field(default=0,ge=0)
    limit:int=Field(default=6000,ge=1,le=12000)


def pointer(value,path):
    if not path:return value
    if not path.startswith('/'):raise DomainError('INVALID_TOOL_INPUT','path必须是JSON Pointer')
    try:
        for raw in path[1:].split('/'):
            # Reject malformed escapes instead of choosing a different field.
            import re
            if re.search(r'~(?![01])',raw):raise ValueError()
            key=raw.replace('~1','/').replace('~0','~')
            if isinstance(value,list):
                if not key.isdigit() or (len(key)>1 and key.startswith('0')):raise ValueError()
                value=value[int(key)]
            elif isinstance(value,dict):value=value[key]
            else:raise ValueError()
        return value
    except (KeyError,IndexError,ValueError,TypeError):
        raise DomainError('RESULT_PATH_NOT_FOUND','已保存回执中没有此字段，请先读取父级路径') from None


def outline(value):
    if not isinstance(value,dict):return {'_preview':True}
    keys=('id','key','status','version','definition_version','revision','enabled','valid','passed','completed','next_step')
    return {k:value[k] for k in keys if k in value and (value[k] is None or type(value[k]) in (bool,int,float) or isinstance(value[k],str) and len(value[k])<=900)}|{'_preview_keys':list(value)[:20],'_key_count':len(value)}


def preview(value,depth=0):
    if isinstance(value,dict):
        if depth>=3:return outline(value)
        result={k:preview(v,depth+1) for k,v in list(value.items())[:18]}
        if len(value)>18:result['_omitted_key_count']=len(value)-18
        return result
    if isinstance(value,list):
        if depth>=3:return {'_item_count':len(value),'_preview':True}
        result=[preview(v,depth+1) for v in value[:4]]
        if len(value)>4:result.append({'_omitted_item_count':len(value)-4})
        return result
    if isinstance(value,str) and len(value)>900:return value[:900]+'…[摘要，完整内容请续读]'
    return value


def observation(result,call_id,execution_id,budget=14000):
    receipt={'call_id':call_id,'execution_id':execution_id}
    if len(json.dumps(result,ensure_ascii=False))<=budget:return {**result,'receipt':receipt}
    brief=preview(result)
    if len(json.dumps(brief,ensure_ascii=False))>budget:
        brief={'ok':result.get('ok'),'value':outline(result.get('value'))}
    return {**brief,'receipt':receipt,'partial':True,
            'stored_result':{'tool':'read_tool_result','call_id':call_id,'path':'/value' if 'value' in result else '', 'offset':0,'limit':6000},
            'notice':'工具执行结果已完整保存。此处为明确标记的摘要，不是失败。按需用read_tool_result指定JSON Pointer及offset/limit续读；不得用摘要替换原文或重演副作用。'}


def read(store,run_id,args):
    query=ResultRead.model_validate(args)
    with store.session() as db:
        if query.call_id is None:
            count=min(query.limit,30)
            rows=list(db.scalars(select(ToolExecution).where(ToolExecution.run_id==run_id,ToolExecution.status=='completed').order_by(ToolExecution.created_at,ToolExecution.id).offset(query.offset).limit(count+1)))
            return {'items':[{'call_id':row.call_id,'tool':row.name,'ok':row.result.get('ok'),'code':row.result.get('code')} for row in rows[:count]],
                    'next_offset':query.offset+count if len(rows)>count else None,'notice':'回执目录；指定call_id及JSON Pointer读取原始结果。'}
        row=db.scalar(select(ToolExecution).where(ToolExecution.run_id==run_id,ToolExecution.call_id==query.call_id,ToolExecution.status=='completed'))
        if not row:raise DomainError('NOT_FOUND','本任务没有这个已完成的工具回执',404)
        value=pointer(row.result,query.path)
    text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,allow_nan=False)
    end=min(query.offset+query.limit,len(text))
    return {'call_id':query.call_id,'path':query.path,'format':'text' if isinstance(value,str) else 'json',
            'content':text[query.offset:end],'offset':query.offset,'total_chars':len(text),
            'next_offset':end if end<len(text) else None,'complete':end>=len(text),
            'keys':list(value)[:50] if isinstance(value,dict) else None,'item_count':len(value) if isinstance(value,list) else None,
            'notice':'这是已保存的快照，不是重新执行工具。分段JSON须读完才是完整文档；可直接指定子字段路径减少读取。'}
