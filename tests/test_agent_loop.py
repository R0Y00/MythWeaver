import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from mythweaver.agent import Agent
from mythweaver.agent_reads import read
from mythweaver.config import Settings
from mythweaver.db import AgentRun
from mythweaver.definitions import DomainError


def agent_for(service):
    return Agent(service,Settings(llm_base_url='https://provider.example/v1',llm_api_key='test-key',llm_model='test-model'))


def reply(name,args,call_id='call-1'):
    return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','tool_calls':[{'id':call_id,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}}]})


def test_reads_then_plans_and_optionally_executes_atomically(service,game,monkeypatch):
    real_client=httpx.AsyncClient
    requests=[]
    def handler(request):
        data=json.loads(request.content)
        requests.append(data)
        if len(requests)==1:
            return reply('query_events',{'limit':3})
        assert data['messages'][-1]['role']=='tool'
        assert 'game.created' in data['messages'][-1]['content']
        return reply('prepare_plan',{'explanation':'按明确请求记录内容','commands':[{'kind':'submit','payload':{'interaction':'_free','data':{'text':'本次管理员授权记录'}}}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(handler),**kw))
    result=asyncio.run(agent_for(service).propose(game['id'],'记录一条内容',execute=True))
    assert result['status']=='applied' and len(service.snapshot(game['id'])['submissions'])==1
    with service.store.session() as db:
        run=db.get(AgentRun,result['run_id'])
        assert run.status=='applied' and run.tool_trace[0]['tool']=='query_events'


def test_unknown_capability_in_plan_returns_feedback_instead_of_killing_turn(service,game,monkeypatch):
    real_client=httpx.AsyncClient;requests=[]
    def handler(request):
        body=json.loads(request.content);requests.append(body)
        if len(requests)==1:return reply('inspect_capabilities',{'kinds':['plugin.create']})
        feedback=json.loads(body['messages'][-1]['content'])
        assert feedback['ok'] is False and feedback['code']=='INVALID_TOOL_INPUT'
        assert '执行任务' in feedback['hint']
        return httpx.Response(200,json={'choices':[{'message':{'content':'此玩法需要随机数插件和实际试玩，请切换执行任务；本轮没有修改游戏。'}}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(handler),**kw))
    before=service.snapshot(game['id'])
    result=asyncio.run(agent_for(service).propose(game['id'],'创建猜数字并测试'))
    assert result['status']=='clarification' and '执行任务' in result['message']
    assert service.snapshot(game['id'])==before
    with service.store.session() as db:
        run=db.get(AgentRun,result['run_id'])
        assert run.status=='answered' and run.tool_trace[0]['ok'] is False
        assert run.tool_trace[0]['arguments']=={'kinds':['plugin.create']}


def test_reading_cannot_cross_game_or_inject_actor(service,game):
    other=service.create_game('其他游戏','')
    service.command(other['id'],'definition',{'version':1,'definition':{'types':{'secret':{'schema':{'type':'object'}}}}},uuid4().hex)
    entity=service.command(other['id'],'entity.create',{'type_key':'secret','data':{'secret':'不可读取'}},uuid4().hex)
    assert read(service,game['id'],'query_records',{'ids':[entity['id']]})==[]
    with pytest.raises(DomainError):
        read(service,game['id'],'query_records',{'actor':'admin'})
    with pytest.raises(DomainError):
        read(service,game['id'],'arbitrary_sql',{})


def test_loop_budget_does_not_leave_writes(service,game,monkeypatch):
    real_client=httpx.AsyncClient
    count=0
    def handler(request):
        nonlocal count
        count+=1
        return reply('query_events',{'limit':1},str(count))
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(handler),**kw))
    with pytest.raises(DomainError) as error:
        asyncio.run(agent_for(service).propose(game['id'],'重复读取'))
    assert error.value.code=='TASK_BUDGET_EXCEEDED' and count==4
    assert not service.snapshot(game['id'])['submissions']


def test_cancel_interrupts_model_before_any_plan_execution(service,game,monkeypatch):
    real_client=httpx.AsyncClient
    async def scenario():
        entered=asyncio.Event()
        async def handler(request):
            entered.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(handler),**kw))
        agent=agent_for(service)
        task=asyncio.create_task(agent.propose(game['id'],'不可继续',execute=True))
        await asyncio.wait_for(entered.wait(),3)
        with service.store.session() as db:
            run=db.scalar(select(AgentRun).where(AgentRun.game_id==game['id']))
            run_id=run.id
        agent.cancel(game['id'],run_id)
        with pytest.raises(DomainError) as error:await task
        assert error.value.code=='TASK_CANCELLED'
        with service.store.session() as db:assert db.get(AgentRun,run_id).status=='cancelled'
        assert not service.snapshot(game['id'])['submissions']
    asyncio.run(scenario())


def test_model_discovers_shared_admin_schemas_and_directly_executes_authorized_settings(service,game,monkeypatch):
    real_client=httpx.AsyncClient
    calls=[]
    def handler(request):
        body=json.loads(request.content)
        calls.append(body)
        kinds=body['tools'][0]['function']['parameters']['properties']['commands']['items']['properties']['kind']['enum']
        assert 'definition.migrate' in kinds and 'checkpoint.restore' in kinds and 'policy.update' in kinds
        if len(calls)==1:
            return reply('inspect_capabilities',{'kinds':['policy.update','participant.update']})
        if len(calls)==2:
            assert 'command_prefix' in body['messages'][-1]['content']
            return reply('list_controls',{'section':'policy'})
        return reply('prepare_plan',{'explanation':'按本次请求修改设置与参与者',
                                    'commands':[{'kind':'policy.update','payload':{'version':0,'command_prefix':'/controlled','paused':True}},
                                                {'kind':'participant.update','payload':{'actor':'qq:7','label':'管理员指定别名','blocked':True,'version':0}}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(handler),**kw))
    result=asyncio.run(agent_for(service).propose(game['id'],'暂停参与，并修改指令和这名参与者',execute=True))
    assert result['status']=='applied' and len(calls)==3
    snapshot=service.snapshot(game['id'])
    assert snapshot['policy']['paused'] and snapshot['policy']['command_prefix']=='/controlled'
    from mythweaver.agent_reads import read
    assert read(service,game['id'],'list_controls',{'section':'participants'})[0]['blocked']
