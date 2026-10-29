from uuid import uuid4

import pytest

from mythweaver.agent import Agent, validate_commands
from mythweaver.config import Settings
from mythweaver.definitions import DomainError


def test_preview_rolls_back_and_apply_is_atomic_idempotent(service, game):
    agent = Agent(service, Settings())
    commands = [
        {"kind":"definition", "payload":{"version":1,"definition":{"types":{"note":{"schema":{"type":"object","properties":{"text":{"type":"string"}}}}}}}},
        {"kind":"entity.create", "payload":{"type_key":"note","data":{"text":"自定义内容"}}},
    ]
    plan = agent.prepare(game['id'], "建立便签", "建立自定义类型和一条便签", commands)
    assert plan['status'] == 'prepared'
    before = service.snapshot(game['id'])
    assert before['game']['version'] == 1
    assert before['entities'] == []
    assert len(before['events']) == 1
    applied = agent.apply(game['id'], plan['id'])
    assert applied['status'] == 'applied'
    assert agent.apply(game['id'], plan['id'])['result'] == applied['result']
    after = service.snapshot(game['id'])
    assert len(after['entities']) == 1 and after['game']['version'] == 2


def test_invalid_plan_does_not_leave_first_command(service, game):
    agent = Agent(service, Settings())
    with pytest.raises(DomainError):
        agent.prepare(game['id'], 'bad', 'bad', [
            {"kind":"submit","payload":{"interaction":"_free","data":{"text":"第一步"}}},
            {"kind":"entity.create","payload":{"type_key":"does_not_exist","data":{}}},
        ])
    assert service.snapshot(game['id'])['submissions'] == []


def test_plan_cannot_apply_after_concurrent_change(service, game):
    agent = Agent(service, Settings())
    plan = agent.prepare(game['id'], '提交', '提交', [{"kind":"submit","payload":{"interaction":"_free","data":{"text":"计划"}}}])
    service.command(game['id'], 'submit', {"interaction":"_free","data":{"text":"另一个已提交操作"}}, uuid4().hex)
    with pytest.raises(DomainError, match='状态已变化|游戏已变化'):
        agent.apply(game['id'], plan['id'])
    assert len(service.snapshot(game['id'])['submissions']) == 1


@pytest.mark.parametrize('command', [
    {"kind":"sql","payload":{"query":"delete all"}},
    {"kind":"submit","payload":{"interaction":"_free","data":{"text":"x"},"actor":"admin"}},
    {"kind":"entity.update","payload":{"type_key":"note","data":{},"version":1}},
])
def test_provider_cannot_grant_tools_or_inject_actor(command):
    with pytest.raises(DomainError):
        validate_commands([command])


def test_missing_model_never_calls_network(service, game):
    import asyncio
    agent = Agent(service, Settings(llm_base_url='',llm_api_key='',llm_model=''))
    with pytest.raises(DomainError) as error:
        asyncio.run(agent.propose(game['id'], '创建内容'))
    assert error.value.code=='MODEL_NOT_CONFIGURED'


def test_compatible_provider_uses_function_plan_and_not_direct_mutation(service, game, monkeypatch):
    import asyncio
    import json
    import httpx
    real_client = httpx.AsyncClient
    def handler(request):
        body = json.loads(request.content)
        assert body['tools'][0]['function']['name'] == 'prepare_plan'
        assert '没有默认战斗' in body['messages'][0]['content']
        assert request.url.path == '/v1/chat/completions'
        return httpx.Response(200, json={"choices":[{"message":{"tool_calls":[{"function":{"name":"prepare_plan","arguments":json.dumps({"explanation":"仅记录内容", "commands":[{"kind":"submit","payload":{"interaction":"_free","data":{"text":"模型建议"}}}]})}}]}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    agent = Agent(service, Settings(llm_base_url='https://provider.example/v1',llm_api_key='test-local',llm_model='selected-by-admin'))
    plan = asyncio.run(agent.propose(game['id'], '记录一句话'))
    assert plan['status'] == 'prepared'
    assert service.snapshot(game['id'])['submissions'] == []

