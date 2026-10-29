import asyncio
import json

import httpx

from mythweaver.agent import Agent
from mythweaver.config import Settings
from mythweaver.db import HarnessTask
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
    assert result['tool_trace'][0]['code']=='INVALID_TOOL_INPUT'
