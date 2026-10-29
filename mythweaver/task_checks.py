"""Evidence checks over durable tool receipts; never execute model expressions."""
import json
from typing import Any,Literal

from pydantic import Field
from sqlalchemy import select

from .db import ToolExecution
from .definitions import DomainError
from .schemas import Input


class EvidenceCheck(Input):
    label:str=Field(min_length=1,max_length=200)
    call_id:str=Field(min_length=1,max_length=200,description='从实际工具结果的receipt.call_id原样复制，不能猜测或引用其他任务')
    path:str=Field(pattern=r'^/value(?:/[^\s]*)?$',max_length=500,description='回执JSON Pointer，例如/value/status或/value/steps/0/ok')
    expected:Any=Field(description='该路径预期的JSON值')


class FinishTask(Input):
    answer:str=Field(min_length=1,max_length=12000)
    checks:list[EvidenceCheck]=Field(min_length=1,max_length=12)
    verification_scope:Literal['receipts','player_conversation','business_acceptance']='receipts'


def verify(db,run_id,checks,verification_scope='receipts'):
    if len(json.dumps(checks,ensure_ascii=False,allow_nan=False))>14000:
        raise DomainError('INVALID_EVIDENCE','验收条件过长')
    verified=[];chat_verified=False;business_verified=False
    for raw in checks:
        check=EvidenceCheck.model_validate(raw)
        row=db.scalar(select(ToolExecution).where(ToolExecution.run_id==run_id,ToolExecution.call_id==check.call_id))
        if not row or row.status!='completed' or row.result.get('ok') is not True or row.name in ('finish_task','record_progress'):
            raise DomainError('INVALID_EVIDENCE','验收必须引用本任务成功完成的实际工具回执，不能引用进度描述')
        current=row.result
        value=row.result.get('value',{})
        if row.name=='workspace_call':value=value.get('result',{})
        actual_business_tool=row.name=='acceptance_run' or (row.name=='workspace_call' and row.arguments.get('tool')=='acceptance_run')
        if actual_business_tool and isinstance(value,dict) and value.get('acceptance',{}).get('ready'):
            from .db import AcceptanceRun,GameDocument,Game
            from .quality import report
            evidence=db.get(AcceptanceRun,value['acceptance'].get('run_id'))
            document=db.get(GameDocument,evidence.document_id) if evidence else None
            if document:
                current_report=report(db,db.get(Game,document.game_id),document)
                business_verified=business_verified or current_report['acceptance']['ready']
        actual_chat_tool=row.name=='simulate_conversation' or (row.name=='workspace_call' and row.arguments.get('tool')=='simulate_conversation')
        if actual_chat_tool and isinstance(value,dict) and value.get('test_layer')=='player_model_conversation':
            steps=value.get('steps',[])
            chat_verified=chat_verified or (value.get('passed') is True and len(steps)>=3 and all(s.get('ok') is True and s.get('checks') for s in steps))
        try:
            for part in check.path[1:].split('/'):
                key=part.replace('~1','/').replace('~0','~')
                if isinstance(current,list):
                    if not key.isdecimal():raise KeyError()
                    current=current[int(key)]
                elif isinstance(current,dict):current=current[key]
                else:raise KeyError()
        except (KeyError,IndexError,TypeError,ValueError):
            raise DomainError('EVIDENCE_MISMATCH','验收路径不存在：'+check.label) from None
        if json.dumps(current,sort_keys=True,ensure_ascii=False,allow_nan=False)!=json.dumps(check.expected,sort_keys=True,ensure_ascii=False,allow_nan=False):
            raise DomainError('EVIDENCE_MISMATCH','实际回执与预期不符：'+check.label)
        verified.append({'label':check.label,'call_id':row.call_id,'execution_id':row.id,'tool':row.name,'path':check.path,'passed':True})
    if verification_scope=='player_conversation' and not chat_verified:
        raise DomainError('CHAT_EVIDENCE_REQUIRED','聊天验收需引用真实玩家模型至少三轮、每轮含预期检查且全部通过的simulate_conversation回执；结构化行动不能替代',409)
    if verification_scope=='business_acceptance' and not business_verified:
        raise DomainError('BUSINESS_EVIDENCE_REQUIRED','整份说明验收须引用当前版本acceptance_run的就绪报告；原文无遗漏映射、关联实现有效、范围内每条规则的用例全部通过',409)
    return verified
