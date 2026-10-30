import asyncio
import json

import httpx
import pytest

from mythweaver.agent import Agent
from mythweaver.config import Settings
from mythweaver.db import HarnessTask,ToolExecution
from mythweaver.harness import Harness
from mythweaver.models import assistant_message


def test_provider_protocol_fields_preserved_without_becoming_answers():
    message={'content':None,'reasoning_content':'opaque-provider-state','tool_calls':[{'id':'a'}],'unrelated':'discard'}
    result=assistant_message(message)
    assert result['reasoning_content']=='opaque-provider-state' and result['tool_calls']==message['tool_calls']
    assert 'unrelated' not in result


def test_planner_round_trip_carries_provider_reasoning(service,game,monkeypatch):
    real=httpx.AsyncClient
    requests=[]
    def response(request):
        body=json.loads(request.content);requests.append(body)
        if len(requests)==1:
            message={'content':None,'reasoning_content':'opaque-provider-state','tool_calls':[{'id':'read-1','type':'function','function':{'name':'query_events','arguments':'{"limit":1}'}}]}
        else:
            assert body['messages'][-2]['reasoning_content']=='opaque-provider-state'
            message={'content':None,'tool_calls':[{'id':'plan-1','type':'function','function':{'name':'prepare_plan','arguments':json.dumps({'explanation':'记录本次授权','commands':[{'kind':'submit','payload':{'data':{'text':'protocol-test'}}}]})}}]}
        return httpx.Response(200,json={'choices':[{'message':message}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(response),**kw))
    settings=Settings(llm_base_url='https://provider.example',llm_api_key='dummy',llm_model='test')
    result=asyncio.run(Agent(service,settings).propose(game['id'],'记录内容',execute=True))
    assert result['status']=='applied' and len(requests)==2


def test_harness_retains_protocol_field_in_private_session_only(service,game,monkeypatch):
    harness=Harness(service,Settings())
    calls=[]
    async def request(messages,tools):
        calls.append(messages)
        if len(calls)==1:
            return {'content':None,'reasoning_content':'opaque-provider-state','tool_calls':[{'id':'progress','type':'function','function':{'name':'record_progress','arguments':'{"completed":"完成读取","next_step":"汇报"}'}}]}
        assert messages[-2]['reasoning_content']=='opaque-provider-state'
        return {'content':'已完成','reasoning_content':'opaque-final-state'}
    monkeypatch.setattr(harness,'request',request)
    run_id=harness.create(game['id'],'验证任务协议')
    result=asyncio.run(harness.run(game['id'],run_id))
    assert result['status']=='answered' and 'opaque-provider-state' not in json.dumps(result)
    with service.store.session() as db:
        task=db.get(HarnessTask,run_id)
        assert task.messages[-1]['reasoning_content']=='opaque-final-state'


def test_harness_retries_malformed_arguments_without_executing_them(service,game,monkeypatch):
    harness=Harness(service,Settings());calls=[]
    async def request(messages,tools):
        calls.append(messages)
        if len(calls)==1:
            return {'tool_calls':[{'id':'broken','function':{'name':'execute_commands','arguments':'{"commands":'}}]}
        assert 'INVALID_TOOL_INPUT' in messages[-1]['content']
        return {'content':'已检查格式错误；本轮没有执行修改。'}
    monkeypatch.setattr(harness,'request',request)
    run_id=harness.create(game['id'],'检验无效参数反馈')
    result=asyncio.run(harness.run(game['id'],run_id))
    assert result['status']=='answered' and result['executions']==[]
    assert next(e for e in result['tool_trace'] if e['tool']=='model.protocol')['code']=='INVALID_TOOL_INPUT'


def test_final_truncation_is_saved_with_budget_and_no_execution(service,game,monkeypatch):
    harness=Harness(service,Settings());calls=[]
    async def request(*args):
        calls.append(True)
        return {'content':'unfinished','_response_meta':{'finish_reason':'length','max_tokens':4096,'usage':{'completion_tokens':4096}}}
    monkeypatch.setattr(harness,'request',request)
    rid=harness.create(game['id'],'检查截断恢复')
    result=asyncio.run(harness.run(game['id'],rid))
    errors=[e for e in result['tool_trace'] if e['tool']=='model.protocol']
    assert result['error_code']=='MODEL_PROTOCOL_STALLED' and len(errors)==3
    assert errors[-1]['code']=='MODEL_OUTPUT_TRUNCATED' and errors[-1]['retrying'] is False
    assert errors[-1]['attempt']==3 and errors[-1]['at'] and not errors[-1]['executed']
    assert not result['executions']
    assert [e for e in result['tool_trace'] if e['tool']=='model.response'][-1]['max_tokens']==4096


def test_temporary_network_failure_recovers_without_replaying_completed_tool(service,game,monkeypatch):
    harness=Harness(service,Settings());calls=[]
    async def no_sleep(*args):pass
    async def request(messages,tools):
        calls.append(messages)
        if len(calls)==1:return {'tool_calls':[{'id':'once','function':{'name':'record_progress','arguments':'{"completed":"进度已保存","next_step":"回复"}'}}]}
        if len(calls)==2:raise httpx.ReadTimeout('provider details must not be logged')
        assert sum(m.get('tool_call_id')=='once' for m in messages)==1
        return {'content':'已恢复，原步骤无需重复。'}
    monkeypatch.setattr(harness,'request',request);monkeypatch.setattr(asyncio,'sleep',no_sleep)
    result=asyncio.run(harness.run(game['id'],harness.create(game['id'],'测试请求恢复')))
    assert result['status']=='answered' and len(calls)==3 and len(result['executions'])==1
    retry=next(e for e in result['tool_trace'] if e['tool']=='model.retry')
    assert retry['code']=='MODEL_REQUEST_TIMEOUT' and retry['retrying']
    assert 'provider details' not in json.dumps(result)


def test_authentication_error_stops_without_retry_and_logs_only_status(service,game,monkeypatch):
    harness=Harness(service,Settings());calls=[]
    async def request(*args):
        calls.append(True)
        response=httpx.Response(401,request=httpx.Request('POST','https://provider.example'),text='secret response')
        response.raise_for_status()
    monkeypatch.setattr(harness,'request',request)
    result=asyncio.run(harness.run(game['id'],harness.create(game['id'],'测试鉴权错误')))
    event=result['tool_trace'][-1]
    assert result['error_code']=='MODEL_HTTP_ERROR' and len(calls)==1
    assert event['http_status']==401 and event['retrying'] is False and 'secret response' not in json.dumps(result)


def test_persistent_network_failure_is_bounded(service,game,monkeypatch):
    harness=Harness(service,Settings());calls=[]
    async def no_sleep(*args):pass
    async def request(*args):calls.append(True);raise httpx.ConnectError('offline')
    monkeypatch.setattr(harness,'request',request);monkeypatch.setattr(asyncio,'sleep',no_sleep)
    result=asyncio.run(harness.run(game['id'],harness.create(game['id'],'测试网络中断')))
    assert result['error_code']=='MODEL_CONNECTION_ERROR' and len(calls)==3
    assert result['tool_trace'][-1]['retrying'] is False and not result['executions']


def test_malformed_provider_body_uses_protocol_recovery(service,game,monkeypatch):
    real=httpx.AsyncClient;requests=[]
    def response(request):
        requests.append(True)
        if len(requests)==1:return httpx.Response(200,text='not json')
        return httpx.Response(200,json={'choices':[{'message':{'content':'已恢复'}}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(response),**kw))
    harness=Harness(service,Settings(llm_base_url='https://provider.example',llm_api_key='dummy',llm_model='test'))
    result=asyncio.run(harness.run(game['id'],harness.create(game['id'],'测试不完整服务响应')))
    assert result['status']=='answered' and len(requests)==2
    assert next(e for e in result['tool_trace'] if e['tool']=='model.protocol')['code']=='INVALID_MODEL_RESPONSE'


def test_null_tool_calls_is_a_valid_plain_answer(service,game,monkeypatch):
    harness=Harness(service,Settings())
    async def request(*args):return {'content':'无需变更，可以直接讨论。','tool_calls':None}
    monkeypatch.setattr(harness,'request',request)
    result=asyncio.run(harness.run(game['id'],harness.create(game['id'],'只是讨论')))
    assert result['status']=='answered' and not result['error_code']
    assert not any(e['tool']=='model.protocol' for e in result['tool_trace'])


@pytest.mark.parametrize('status',[429,503])
def test_temporary_http_errors_retry_before_any_tool(service,game,monkeypatch,status):
    harness=Harness(service,Settings());calls=[]
    async def no_sleep(*args):pass
    async def request(*args):
        calls.append(True)
        if len(calls)==1:httpx.Response(status,request=httpx.Request('POST','https://provider.example')).raise_for_status()
        return {'content':'服务已恢复'}
    monkeypatch.setattr(harness,'request',request);monkeypatch.setattr(asyncio,'sleep',no_sleep)
    result=asyncio.run(harness.run(game['id'],harness.create(game['id'],'测试服务暂时不可用')))
    assert result['status']=='answered' and len(calls)==2 and not result['executions']
    assert next(e for e in result['tool_trace'] if e['tool']=='model.retry')['http_status']==status


def test_cancel_during_retry_does_not_restart_task(service,game,monkeypatch):
    harness=Harness(service,Settings());calls=[]
    async def scenario():
        waiting=asyncio.Event()
        async def sleep(*args):waiting.set();await asyncio.Event().wait()
        async def request(*args):calls.append(True);raise httpx.ReadTimeout('timeout')
        monkeypatch.setattr(harness,'request',request);monkeypatch.setattr(asyncio,'sleep',sleep)
        rid=harness.create(game['id'],'测试恢复中取消')
        task=asyncio.create_task(harness.run(game['id'],rid))
        await asyncio.wait_for(waiting.wait(),5)
        task.cancel();return await task
    result=asyncio.run(scenario())
    assert result['status']=='cancelled' and result['error_code']=='TASK_CANCELLED' and len(calls)==1


def test_valid_handoff_response_breaks_consecutive_protocol_failure_count(service,game,monkeypatch):
    service.command(game['id'],'policy.update',{'version':0,'player_agent_enabled':True},'test-enable-player')
    harness=Harness(service,Settings());calls=[];rid=harness.create(game['id'],'分别统计交接和响应错误')
    with service.store.session.begin() as db:
        db.add(ToolExecution(run_id=rid,call_id='saved',name='execute_commands',arguments={'commands':[{'kind':'policy.update','payload':{'player_instructions':'待验收说明'}}]},status='completed',result={'ok':True,'value':{}}))
    async def request(*args):
        calls.append(True)
        if len(calls) in (1,3,4):return {'content':''}
        if len(calls)==2:return {'content':'还缺一项业务决定。','tool_calls':None}
        return {'tool_calls':[{'id':'ask','function':{'name':'ask_admin','arguments':json.dumps({'question':'允许玩家之间交易吗？','reason':'原文没有定义这项业务规则。','completed':['说明已保存，尚未验收']})}}]}
    monkeypatch.setattr(harness,'request',request)
    result=asyncio.run(harness.run(game['id'],rid))
    assert result['status']=='awaiting_input' and not result['error_code'] and len(calls)==5
    assert [e['attempt'] for e in result['tool_trace'] if e['tool']=='model.protocol']==[1,1,2]
