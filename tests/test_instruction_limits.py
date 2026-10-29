import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from mythweaver.api import AgentRequest,create_app
from mythweaver.agent_reads import read as read_tool
from mythweaver.command_catalog import validate_command
from mythweaver.config import Settings
from mythweaver.db import HarnessTask
from mythweaver.definitions import DomainError
from mythweaver.harness import Harness,protocol_issue
from mythweaver.instructions import page
from mythweaver.platform_rules import Rules,RulesPatch,read,update
from mythweaver.player import PlayerAgent,view
from mythweaver.playtest import simulate,simulate_conversation
from mythweaver.schemas import PolicyInput


def test_instruction_schemas_do_not_limit_characters_and_keep_exact_text():
    text='完整说明🙂\n'*20000
    for model,values in ((Rules,{'player_instructions':text,'agent_instructions':text}),
                         (RulesPatch,{'player_instructions':text,'agent_instructions':text}),
                         (PolicyInput,{'version':0,'player_instructions':text,'instructions':text}),
                         (AgentRequest,{'prompt':text})):
        result=model.model_validate(values).model_dump()
        for key,value in values.items():assert result[key]==value
    assert validate_command('policy.update',{'version':0,'player_instructions':text})['player_instructions']==text
    for key in ('player_instructions','agent_instructions'):assert 'maxLength' not in Rules.model_json_schema()['properties'][key]


def test_instruction_page_is_explicit_and_does_not_modify_source():
    original={'player_instructions':'甲'*20000,'instructions':'乙'*20000,'paused':False}
    first=page(original);assert first['player_instructions']=='甲'*8000
    assert first['instruction_pages']['player_instructions']['total_chars']==20000
    assert first['instruction_pages']['player_instructions']['next_offset']==8000
    assert page(original,16000)['player_instructions']=='甲'*4000
    assert page(original,16000)['instruction_pages']['player_instructions']['next_offset'] is None
    assert original['player_instructions']=='甲'*20000


def test_long_instructions_saved_and_delivered_without_context_budget_rejection(service,game):
    global_text='全局完整规则'*12000;local_text='本局完整规则'*12000
    with service.store.session.begin() as db:update(db,0,{'player_instructions':global_text})
    service.command(game['id'],'policy.update',{'version':0,'player_agent_enabled':True,'player_instructions':local_text},'policy')
    with service.store.session() as db:
        result=view(service,db,service.game(db,game['id']),'qq:1')
        assert global_text in result['instructions'] and local_text in result['instructions']
        assert read(db)['rules']['player_instructions']==global_text
    tool=read_tool(service,game['id'],'list_controls',{'section':'policy','instruction_offset':16000})
    assert tool['instruction_pages']['player_instructions']['total_chars']==len(local_text)
    assert tool['player_instructions']==local_text[16000:24000]


def test_long_platform_update_is_not_reported_as_tool_result_too_large(service,game,monkeypatch):
    text='长说明'*20000;harness=Harness(service,Settings());requests=[]
    async def request(messages,tools):
        requests.append(messages)
        if len(requests)>1:return {'content':'已保存全文。'}
        return {'tool_calls':[{'id':'save-long','type':'function','function':{'name':'platform_rules_update','arguments':json.dumps({'version':0,'changes':{'player_instructions':text}})}}]}
    monkeypatch.setattr(harness,'request',request)
    rid=harness.create(game['id'],'保存长说明',approval_mode='auto')
    result=asyncio.run(harness.run(game['id'],rid))
    assert result['status']=='answered' and result['executions'][0]['result']['ok']
    metadata=result['executions'][0]['result']['value']['instruction_pages']['player_instructions']
    assert metadata['total_chars']==len(text)
    with service.store.session() as db:assert read(db)['rules']['player_instructions']==text


def test_http_long_platform_and_game_instructions_preserve_full_text(store,service,game):
    text='长规则'*25000;settings=Settings()
    with TestClient(create_app(settings,store)) as client:
        client.headers['X-MythWeaver']='1'
        client.post('/api/login',json={'password':settings.admin_password}).raise_for_status()
        response=client.put('/api/platform/rules',json={'version':0,'changes':{'player_instructions':text}})
        assert response.status_code==200 and response.json()['rules']['player_instructions']==text
        response=client.put('/api/games/'+game['id']+'/policy',json={'version':0,'instructions':text,'player_instructions':text})
        assert response.status_code==200 and response.json()['instructions']==text


def test_execution_budget_can_be_set_by_agent_and_zero_means_unlimited(service,game,monkeypatch):
    with service.store.session.begin() as db:update(db,0,{'agent_max_steps':1,'agent_tool_calls_per_turn':1})
    harness=Harness(service,Settings());requests=[]
    async def request(messages,tools):
        requests.append(messages)
        if len(requests)>1:return {'content':'继续完成。'}
        return {'tool_calls':[{'id':'read','type':'function','function':{'name':'platform_rules_read','arguments':'{}'}}]}
    monkeypatch.setattr(harness,'request',request)
    rid=harness.create(game['id'],'读取规则')
    result=asyncio.run(harness.run(game['id'],rid));assert result['status']=='budget_exhausted' and len(requests)==1
    with service.store.session.begin() as db:update(db,1,{'agent_max_steps':0,'agent_timeout_seconds':0,'agent_tool_calls_per_turn':0})
    harness.resume(game['id'],rid,'继续')
    with service.store.session() as db:assert db.get(HarnessTask,rid).max_steps==0
    result=asyncio.run(harness.run(game['id'],rid));assert result['status']=='answered'
    many={'tool_calls':[{'id':str(i),'function':{'name':'query_records','arguments':'{}'}} for i in range(6)]}
    assert protocol_issue(many)=='INVALID_TOOL_BATCH'
    assert protocol_issue(many,0) is None and protocol_issue(many,6) is None
    with pytest.raises(ValidationError):Rules(agent_max_steps=-1)


def test_player_data_budget_can_be_disabled_without_changing_instructions(service,game):
    service.command(game['id'],'policy.update',{'version':0,'player_agent_enabled':True},'enable')
    with service.store.session.begin() as db:update(db,0,{'player_data_context_chars':1})
    with service.store.session() as db:
        with pytest.raises(DomainError) as error:view(service,db,service.game(db,game['id']),'qq:1',compact=True)
        assert error.value.code=='CONTEXT_TOO_LARGE'
    with service.store.session.begin() as db:update(db,1,{'player_data_context_chars':0})
    with service.store.session() as db:assert view(service,db,service.game(db,game['id']),'qq:1',compact=True)['records']==[]


def test_simulation_limits_are_administrator_configurable(service,game,monkeypatch):
    service.command(game['id'],'definition',{'version':1,'definition':{'types':{},'interactions':{'play':{
        'mode':'fixed','response':'成功','player_enabled':True,'group_enabled':True}}}},'define')
    service.command(game['id'],'policy.update',{'version':0,'player_agent_enabled':True},'enable')
    with service.store.session.begin() as db:update(db,0,{'simulation_action_limit':1,'simulation_chat_limit':1})
    actions=[{'interaction':'play','data':{}}]*21
    with pytest.raises(DomainError):simulate(service,game['id'],'qq:1',actions)
    arguments={'actor':'qq:1','audience':'group','messages':[{'message':'行动','expect_interaction':'play'}]*9}
    with pytest.raises(DomainError):asyncio.run(simulate_conversation(service,Settings(),game['id'],arguments))
    with service.store.session.begin() as db:update(db,1,{'simulation_action_limit':0,'simulation_chat_limit':0})
    assert len(simulate(service,game['id'],'qq:1',actions)['steps'])==21
    async def request(self,context,history,prompt):return {'tool_calls':[{'function':{'name':'submit_action','arguments':'{"interaction":"play","data":{}}'}}]}
    monkeypatch.setattr(PlayerAgent,'request',request)
    result=asyncio.run(simulate_conversation(service,Settings(),game['id'],arguments))
    assert result['passed'] and len(result['steps'])==9


def test_model_routing_no_longer_caps_output_at_16384_or_timeout_at_120(store):
    from mythweaver.models import Models
    settings=Settings()
    with TestClient(create_app(settings,store)) as client:
        client.headers['X-MythWeaver']='1'
        client.post('/api/login',json={'password':settings.admin_password}).raise_for_status()
        provider=client.post('/api/models/providers',json={'name':'test','base_url':'https://provider.example/v1','api_key':'dummy-test-key','models':['test']}).json()
        route={'planner':{'provider_id':provider['id'],'model':'test','max_tokens':65536,'timeout':600}}
        response=client.put('/api/models/routing',json={'version':0,'routing':route})
        assert response.status_code==200
        assert Models(store,settings).resolve().max_tokens==65536
        assert Models(store,settings).resolve().timeout==600
        route['planner']['timeout']=0
        assert client.put('/api/models/routing',json={'version':1,'routing':route}).status_code==200
        assert Models(store,settings).resolve().timeout is None
