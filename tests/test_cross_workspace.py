import asyncio
import json

import pytest
from sqlalchemy import select

from mythweaver.approvals import decide
from mythweaver.config import Settings
from mythweaver.db import AgentRun,ToolExecution
from mythweaver.definitions import DomainError
from mythweaver.game_lifecycle import delete_game
from mythweaver.harness import Harness
from mythweaver.workspaces import validate


def setup_call(service,game,monkeypatch,tool='execute_commands',arguments=None,auto=False):
    target=service.create_game('跨空间目标','another')
    harness=Harness(service,Settings())
    args={'game_id':target['id'],'tool':tool,'arguments':arguments if arguments is not None else {'explanation':'只写目标空间','commands':[{'kind':'submit','payload':{'interaction':'_free','data':{'text':'target only'}}}]}}
    async def request(messages,tools):
        if messages[-1]['role']=='tool':return {'content':'已按目标空间处理。'}
        return {'tool_calls':[{'id':'cross-call','type':'function','function':{'name':'workspace_call','arguments':json.dumps(args)}}]}
    monkeypatch.setattr(harness,'request',request)
    rid=harness.create(game['id'],'修改另一个空间',approval_mode='auto' if auto else 'manual')
    return target,harness,rid


def test_cross_workspace_approval_changes_only_target_and_keeps_origin_receipt(service,game,monkeypatch):
    target,harness,rid=setup_call(service,game,monkeypatch)
    first=asyncio.run(harness.run(game['id'],rid))
    assert first['status']=='awaiting_approval'
    assert first['executions'][0]['approval']['target_game']=={'id':target['id'],'name':target['name']}
    assert not service.snapshot(target['id'])['submissions']
    decide(harness,game['id'],rid,first['executions'][0]['id'],'approve')
    result=asyncio.run(harness.run(game['id'],rid))
    assert result['status']=='answered'
    assert result['executions'][0]['result']['value']['game_id']==target['id']
    assert len(service.snapshot(target['id'])['submissions'])==1
    assert not service.snapshot(game['id'])['submissions']
    assert result['game_id']==game['id']


def test_cross_workspace_reads_do_not_require_approval(service,game,monkeypatch):
    target,harness,rid=setup_call(service,game,monkeypatch,'query_records',{})
    result=asyncio.run(harness.run(game['id'],rid))
    assert result['status']=='answered' and result['executions'][0]['result']['ok']
    assert not result['executions'][0]['approval']
    games=asyncio.run(harness.execute(game['id'],rid,'workspace_list',{'query':'跨空间'},'list'))
    assert [g['id'] for g in games['games']]==[target['id']]
    inspected=asyncio.run(harness.execute(game['id'],rid,'workspace_inspect',{'game_id':target['id']},'inspect'))
    assert inspected['game']['id']==target['id'] and any(t['name']=='execute_commands' for t in inspected['tools'])


def test_target_state_change_invalidates_approval(service,game,monkeypatch):
    target,harness,rid=setup_call(service,game,monkeypatch)
    first=asyncio.run(harness.run(game['id'],rid))
    decide(harness,game['id'],rid,first['executions'][0]['id'],'approve')
    service.command(target['id'],'submit',{'interaction':'_free','data':{'text':'concurrent'}},'other')
    result=asyncio.run(harness.run(game['id'],rid))
    assert result['executions'][0]['result']['code']=='APPROVAL_STALE'
    assert len(service.snapshot(target['id'])['submissions'])==1


@pytest.mark.parametrize('tool',['workspace_call','finish_task','platform_rules_update','unknown_tool'])
def test_nested_or_unknown_target_tools_are_rejected(service,game,tool):
    harness=Harness(service,Settings())
    with pytest.raises(DomainError):validate(harness,{'game_id':game['id'],'tool':tool,'arguments':{}})


def test_cross_workspace_auto_approval_and_running_target_delete_guard(service,game,monkeypatch):
    target,harness,rid=setup_call(service,game,monkeypatch,auto=True)
    result=asyncio.run(harness.run(game['id'],rid))
    assert result['executions'][0]['approval']['decision']=='auto'
    with service.store.session.begin() as db:
        row=db.scalar(select(ToolExecution).where(ToolExecution.run_id==rid));row.status='running'
    with pytest.raises(DomainError) as error:delete_game(service,target['id'],target['version'],target['name'])
    assert error.value.code=='GAME_BUSY'


def test_create_and_delete_other_space_in_one_conversation(service,game,monkeypatch):
    harness=Harness(service,Settings());created=[]
    async def request(messages,tools):
        if messages[-1]['role']!='tool':
            name,args='workspace_create',{'name':'临时空间','description':'created by agent'}
        else:
            result=json.loads(messages[-1]['content'])['value']
            if result.get('status')=='deleted':return {'content':'临时空间已删除。'}
            created.append(result['id'])
            name,args='workspace_delete',{'game_id':result['id'],'version':result['version'],'confirmation':result['name']}
        return {'tool_calls':[{'id':name,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
    monkeypatch.setattr(harness,'request',request)
    rid=harness.create(game['id'],'建立临时空间然后删除')
    first=asyncio.run(harness.run(game['id'],rid));assert first['status']=='awaiting_approval'
    assert not created
    decide(harness,game['id'],rid,first['executions'][0]['id'],'approve')
    second=asyncio.run(harness.run(game['id'],rid));assert second['status']=='awaiting_approval'
    assert service.snapshot(created[0])['game']['name']=='临时空间'
    pending=next(e for e in second['executions'] if e['status']=='awaiting_approval')
    assert pending['approval']['target_game']['id']==created[0]
    decide(harness,game['id'],rid,pending['id'],'approve')
    result=asyncio.run(harness.run(game['id'],rid));assert result['status']=='answered'
    with pytest.raises(DomainError):service.snapshot(created[0])
    assert service.snapshot(game['id'])['game']['id']==game['id']


def test_current_running_conversation_cannot_delete_its_own_space(service,game):
    harness=Harness(service,Settings());rid=harness.create(game['id'],'active')
    with pytest.raises(DomainError) as error:
        asyncio.run(harness.execute(game['id'],rid,'workspace_delete',{'game_id':game['id'],'version':game['version'],'confirmation':game['name']},'delete'))
    assert error.value.code=='GAME_BUSY'
    assert service.snapshot(game['id'])['game']['id']==game['id']
