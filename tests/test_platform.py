from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from mythweaver.api import create_app
from mythweaver.config import Settings
from mythweaver.db import Channel, Command, Event, Inbox, Outbox
from mythweaver.definitions import DomainError, validate_definition


def key():
    return uuid4().hex


def definition(service, game, value):
    return service.command(game["id"], "definition", {"version": game["version"], "definition": value}, key())


def test_empty_game_manual_participation(service, game):
    snapshot = service.snapshot(game["id"])
    assert snapshot["game"]["definition"] == {"types": {}, "interactions": {}}
    result = service.command(game["id"], "submit", {"interaction": "_free", "data": {"text": "自由文字"}}, key(), actor="qq:1")
    assert result["status"] == "pending"
    outcome = service.command(game["id"], "decide", {"id": result["id"], "decision": "applied", "result": "管理员现场决定"}, key())
    assert outcome["result"] == "管理员现场决定"


def test_custom_data_schema_and_optimistic_lock(service, game):
    schema = {"type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"], "additionalProperties": False}
    definition(service, game, {"types": {"custom": {"label": "完全自定义", "schema": schema}}})
    row = service.command(game["id"], "entity.create", {"type_key": "custom", "data": {"note": "原始"}}, key())
    payload = {"type_key": "custom", "id": row["id"], "version": 1, "data": {"note": "修改"}}
    service.command(game["id"], "entity.update", payload, key())
    with pytest.raises(DomainError, match="对象已被修改"):
        service.command(game["id"], "entity.update", payload, key())
    with pytest.raises(DomainError, match="不符合定义"):
        service.command(game["id"], "entity.create", {"type_key": "custom", "data": {"note": 7}}, key())


def test_definition_change_invalidates_pending_and_checks_existing_data(service, game):
    pending = service.command(game["id"], "submit", {"interaction": "_free", "data": {"text": "等待"}}, key())
    definition(service, game, {"types": {"thing": {"schema": {"type": "object", "properties": {"n": {"type": "number"}}}}}})
    assert service.snapshot(game["id"])["submissions"][0]["status"] == "stale"
    with pytest.raises(DomainError):
        service.command(game["id"], "decide", {"id": pending["id"], "decision": "applied", "result": "旧结果"}, key())
    service.command(game["id"], "entity.create", {"type_key": "thing", "data": {"n": 1}}, key())
    with pytest.raises(DomainError, match="现存数据"):
        service.command(game["id"], "definition", {"version": 2, "definition": {"types": {}, "interactions": {}}}, key())
    assert service.snapshot(game["id"])["game"]["version"] == 2


def test_three_modes_and_disabled_capabilities(service, game):
    definition(service, game, {"interactions": {
        "record": {"mode": "record"}, "fixed": {"mode": "fixed", "response": "预定结果"},
        "manual": {"mode": "manual"}, "off": {"enabled": False}}})
    for interaction, status in (("record", "applied"), ("fixed", "applied"), ("manual", "pending")):
        result = service.command(game["id"], "submit", {"interaction": interaction, "data": {}}, key(), actor="qq:1")
        assert result["status"] == status
        if interaction == "fixed":
            assert result["result"] == "预定结果"
        assert "definition" not in result and "actor" not in result
    with pytest.raises(DomainError):
        service.command(game["id"], "submit", {"interaction": "off", "data": {}}, key())


def test_concurrent_duplicate_submission_only_commits_once(service, store, game):
    payload = {"interaction": "_free", "data": {"text": "同一条消息"}}
    command_key = key()
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: service.command(game["id"], "submit", payload, command_key, actor="qq:5"), range(10)))
    assert len({row["id"] for row in results}) == 1
    with store.session() as db:
        assert db.scalar(select(func.count()).select_from(Command)) == 1
    with pytest.raises(DomainError, match="不同请求"):
        service.command(game["id"], "submit", {"interaction": "_free", "data": {"text": "不同内容"}}, command_key, actor="qq:5")


def test_concurrent_decision_only_applies_once(service, game):
    pending = service.command(game["id"], "submit", {"interaction": "_free", "data": {"text": "选项"}}, key())
    def decide(_):
        try:
            service.command(game["id"], "decide", {"id": pending["id"], "decision": "applied", "result": "确认"}, key())
            return "ok"
        except DomainError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(decide, range(2))) == ["STATE_STALE", "ok"]


def test_actor_and_cross_game_boundaries(service, game):
    with pytest.raises(DomainError, match="无管理权限"):
        service.command(game["id"], "definition", {"version": 1, "definition": {}}, key(), actor="qq:123")
    other = service.create_game("另一游戏", "")
    row = service.command(other["id"], "submit", {"interaction": "_free", "data": {"text": "私有"}}, key())
    with pytest.raises(DomainError, match="不存在"):
        service.command(game["id"], "decide", {"id": row["id"], "decision": "applied", "result": "非法"}, key())


def test_rollback_does_not_leave_events(service, store, game):
    definition(service, game, {"types": {"custom": {"schema": {"type": "object", "required": ["value"]}}}})
    with store.session() as db:
        before = db.scalar(select(func.count()).select_from(Event))
    with pytest.raises(DomainError):
        service.command(game["id"], "entity.create", {"type_key": "custom", "data": {}}, key())
    with store.session() as db:
        assert db.scalar(select(func.count()).select_from(Event)) == before


@pytest.mark.parametrize("schema", [{"type": "object", "$ref": "file:///secret"}, {"type": "object", "properties": {"x": {"$ref": "https://example.com"}}}, {"type": "object", "patternProperties": {".*": {}}}])
def test_schema_cannot_fetch_external_resources(schema):
    with pytest.raises(DomainError):
        validate_definition({"types": {"bad": {"schema": schema}}})


def test_api_auth_csrf_and_hidden_private_state(store, service, game):
    settings = Settings()
    app = create_app(settings, store)
    with TestClient(app) as client:
        assert client.get('/api/games').status_code == 401
        assert client.post('/api/login', json={"password": settings.admin_password}).status_code == 403
        assert client.post('/api/login', json={"password": settings.admin_password}, headers={"x-mythweaver": "1", "origin": "https://evil.test"}).status_code == 403
        response = client.post('/api/login', json={"password": settings.admin_password}, headers={"x-mythweaver": "1"})
        assert response.status_code == 200 and "HttpOnly" in response.headers['set-cookie']
        assert client.get('/api/games').status_code == 200
        assert client.get('/api/games/'+game['id']).headers['cache-control'] == 'no-store'
        assert client.post('/api/games', json={"name":"x", "actor":"admin"}, headers={"x-mythweaver":"1"}).status_code == 422


def test_onebot_auth_echo_and_qq_inbox(store, service, game):
    settings = Settings()
    app = create_app(settings, store)
    bot = app.state.bot
    with store.session.begin() as db:
        db.add(Channel(group_id="100", game_id=game["id"]))
    with TestClient(app) as client:
        with pytest.raises(Exception):
            with client.websocket_connect('/channels/onebot/v11/ws'):
                pass
        with client.websocket_connect('/channels/onebot/v11/ws', headers={"authorization": "Bearer "+settings.onebot_token, "x-self-id":settings.bot_id}) as ws:
            with ThreadPoolExecutor() as pool:
                future = pool.submit(client.portal.call, bot.call, "get_login_info", {})
                request = ws.receive_json()
                ws.send_json({"status":"ok", "retcode":0, "echo":request['echo'], "data":{"user_id":settings.bot_id}})
                assert future.result(timeout=5) == {"user_id":settings.bot_id}
            frame = {"self_id":settings.bot_id, "post_type":"message", "message_type":"group", "group_id":100, "user_id":200,"message_id":1,"message":[{"type":"text","data":{"text":"/mw 提交 测试输入"}}]}
            # Exercise durable reception synchronously without sending to real QQ.
            bot.accept_event(frame)
            bot.accept_event(frame)
            bot.process_inbox()
            with store.session() as db:
                assert db.scalar(select(func.count()).select_from(Inbox)) == 1
                assert db.scalar(select(func.count()).select_from(Outbox)) == 1
            assert len(service.snapshot(game['id'])['submissions']) == 1


def test_ordinary_chat_and_unbound_group_are_not_stored(store, service, game):
    from mythweaver.onebot import OneBot
    bot = OneBot(store, service, Settings())
    for group, content in ((100,"普通聊天"),(999,"/mw 提交 不应收录")):
        bot.accept_event({"message_type":"group","group_id":group,"user_id":3,"message_id":key(),"message":[{"type":"text","data":{"text":content}}]})
    with store.session() as db:
        assert db.scalar(select(func.count()).select_from(Inbox)) == 0


def test_unbind_cancels_unprocessed_input_and_queued_reply(store, service, game):
    from mythweaver.onebot import OneBot
    bot = OneBot(store, service, Settings())
    with store.session.begin() as db:
        db.add(Inbox(key='1:100:1',game_id=game['id'],actor='qq:3',content='提交 不应执行'))
        db.add(Outbox(group_id='100',game_id=game['id'],text='不应发送'))
    bot.process_inbox()
    assert bot.next_delivery() is None
    with store.session() as db:
        assert db.scalar(select(Inbox)).status == 'cancelled'
        assert db.scalar(select(Outbox)).status == 'cancelled'
    assert service.snapshot(game['id'])['submissions'] == []


def test_additive_migration_preserves_existing_outbox(store, game):
    from sqlalchemy import text
    from mythweaver.db import SchemaMigration
    with store.session.begin() as db:
        db.add(Outbox(group_id='100',game_id=game['id'],text='旧投递'))
        db.flush()
        db.execute(text('DELETE FROM schema_migrations'))
        db.execute(text('ALTER TABLE outbox DROP COLUMN game_id'))
    store.initialize()
    with store.session() as db:
        row = db.scalar(select(Outbox))
        assert row.text == '旧投递' and row.status == 'cancelled'
        assert db.get(SchemaMigration,1)

