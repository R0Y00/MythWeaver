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
    verification_scope:Literal['receipts','player_conversation','business_acceptance','player_handoff']='receipts'


def qualifies_player_dialogue(game,case):
    steps=case.get('steps',[])
    if not (case.get('passed') is True and len(steps)>=3 and all(s.get('ok') and s.get('checks') for s in steps)):return False
    specs={key:spec for key,spec in game.definition.get('interactions',{}).items() if spec.get('enabled',True) and spec.get('player_enabled')}
    actual=[]
    for step in steps:
        action=step.get('action') or {}
        for item in action.get('actions',[action]):
            if item.get('interaction') in specs and ('action' in step['checks'] or 'interaction' in step['checks']):actual.append((step,specs[item['interaction']]))
    if specs and not actual:return False
    if any(spec.get('effects') or spec.get('extension',{}).get('writes') for _,spec in actual):
        if not any(any(k.startswith('record_') for k in step['checks']) for step,_ in actual):return False
    contracts=[s.get('player_contract',{}) for s in specs.values()]
    if any(c.get('fields') for c in contracts) and not any(any(k.startswith('input_') for k in step['checks']) for step,_ in actual):return False
    if any(c.get('context_mode','conversation')=='conversation' and any(f.get('allow_history',True) for f in c.get('fields',[])) for c in contracts):
        if not any(s.get('history_used') and s['checks'].get('history_used') is True for s in steps):return False
    return True


def player_targets(db,run_id):
    """A player-facing modification cannot be 'accepted' using save receipts alone."""
    from .db import AgentRun,Game
    from .policy import policy_for
    run=db.get(AgentRun,run_id);targets=set()
    if not run:return targets
    for row in db.scalars(select(ToolExecution).where(ToolExecution.run_id==run_id,ToolExecution.status=='completed')):
        if row.result.get('ok') is not True:continue
        name,args,target=row.name,row.arguments,run.game_id
        if name=='workspace_call':name,args,target=args.get('tool'),args.get('arguments',{}),args.get('game_id')
        game=db.get(Game,target)
        if not game or not policy_for(db,target)[0]['player_agent_enabled']:continue
        relevant=False
        if name=='execute_commands':
            for command in args.get('commands',[]):
                kind=command.get('kind');payload=command.get('payload',{})
                if kind=='policy.update' and any(k in payload for k in ('player_agent_enabled','player_instructions','group_input','group_trigger','private_trigger')):relevant=True
                if kind=='module.put':
                    manifest=payload.get('manifest',{})
                    relevant=relevant or any(s.get('player_enabled') for s in manifest.get('interactions',{}).values()) or any(s.get('player_access') for s in manifest.get('types',{}).values())
                if kind in ('module.state','module.remove','definition','definition.migrate'):relevant=True
        if name in ('plugin_install','plugin_state'):
            from .db import PluginRelease
            release=db.get(PluginRelease,args.get('id')) if args.get('id') else None
            relevant=bool(release and any(s.get('extension',{}).get('plugin_key')==release.key for s in game.definition.get('interactions',{}).values() if s.get('player_enabled')))
        if relevant:targets.add(target)
    return targets


def task_documents(db,run_id):
    from .db import AgentRun
    run=db.get(AgentRun,run_id);result=set()
    if not run:return result
    for row in db.scalars(select(ToolExecution).where(ToolExecution.run_id==run_id,ToolExecution.status=='completed')):
        if row.result.get('ok') is not True:continue
        name,args,target=row.name,row.arguments,run.game_id
        if name=='workspace_call':name,args,target=args.get('tool'),args.get('arguments',{}),args.get('game_id')
        if name=='execute_commands':
            for command in args.get('commands',[]):
                if command.get('kind')=='requirements.put':result.add((target,command.get('payload',{}).get('key')))
    return result


def verify(db,run_id,checks,verification_scope='receipts'):
    if len(json.dumps(checks,ensure_ascii=False,allow_nan=False))>14000:
        raise DomainError('INVALID_EVIDENCE','验收条件过长')
    verified=[];chat_verified=False;business_verified=False;handoffs=set();chats=set()
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
                if current_report['acceptance']['ready'] and any(qualifies_player_dialogue(db.get(Game,document.game_id),case) for case in current_report['acceptance']['scenarios'].values()):
                    chats.add(document.game_id)
        actual_chat_tool=row.name=='simulate_conversation' or (row.name=='workspace_call' and row.arguments.get('tool')=='simulate_conversation')
        if actual_chat_tool and isinstance(value,dict) and value.get('test_layer')=='player_model_conversation':
            steps=value.get('steps',[])
            this_chat=value.get('passed') is True and len(steps)>=3 and all(s.get('ok') is True and s.get('checks') for s in steps)
            chat_verified=chat_verified or this_chat
            if this_chat and value.get('configuration_current') and value.get('game_id'):
                from .quality import config_stamp
                from .db import Game
                game=db.get(Game,value['game_id'])
                if game and value.get('configuration_stamp')==config_stamp(db,game) and qualifies_player_dialogue(game,value):chats.add(game.id)
        actual_handoff_tool=row.name=='inspect_player_agent' or (row.name=='workspace_call' and row.arguments.get('tool')=='inspect_player_agent')
        if actual_handoff_tool and isinstance(value,dict) and value.get('enabled') and not value.get('blocking_issues'):
            from .quality import config_stamp
            from .db import Game
            game=db.get(Game,value.get('game_id')) if value.get('game_id') else None
            if game and value.get('configuration_stamp')==config_stamp(db,game):handoffs.add(game.id)
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
    required=player_targets(db,run_id)
    if (required and not required<=handoffs&chats) or (verification_scope=='player_handoff' and not handoffs&chats):
        raise DomainError('PLAYER_HANDOFF_REQUIRED','验收须引用当前inspect_player_agent无阻塞报告和至少三轮真实对话：有开放行动就实际调用并断言输入，写资源要断言记录；允许跨轮补充的契约必须用expect_history_used=true核对实际承接历史。不能用三次问候或每次重复完整句子替代。缺业务资料用ask_admin。',409)
    for target,key in task_documents(db,run_id):
        if target not in required:continue
        from .quality import latest,report
        from .db import Game
        document=latest(db,target,key)
        current=report(db,db.get(Game,target),document) if document else None
        if not current or not current['acceptance']['ready']:
            details=[]
            if current:
                details=[c['key']+'：'+'；'.join(c['verification_issues']) for c in current['coverage'] if c['verification_issues']][:5]
                if current['unmapped_source']:details.append('未映射原文：'+'；'.join(current['unmapped_source'][:3]))
            raise DomainError('BUSINESS_EVIDENCE_REQUIRED',('本任务保存的业务原文尚未验收，不能跳过覆盖表宣布完成。'+('；'.join(details))+'。请requirements_read补齐映射与当前验收；技术遗漏自行修复，真正缺业务规则用ask_admin。')[:2400],409)
    return verified
