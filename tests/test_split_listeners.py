import asyncio
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mythweaver.api import create_app
from mythweaver.config import Settings
from mythweaver.server import SplitPorts,bind,listeners


def settings():
    return Settings(admin_password='test-password-only',session_secret='x'*64,onebot_token='y'*64,bot_id='123',public_origin='auto')


def test_default_ports_are_separate_and_custom_web_does_not_move_onebot():
    assert listeners({})=={'API_PORT':8001,'ONEBOT_PORT':8080,'API_HOST':'127.0.0.1','ONEBOT_HOST':'127.0.0.1'}
    result=listeners({'API_PORT':'19001','API_HOST':'0.0.0.0'})
    assert result['API_PORT']==19001 and result['ONEBOT_PORT']==8080 and result['ONEBOT_HOST']=='127.0.0.1'


@pytest.mark.parametrize('config',[{'API_PORT':'8080'},{'ONEBOT_PORT':'8001'},{'ONEBOT_PORT':'bad'},{'ONEBOT_PORT':'65536'},{'ONEBOT_HOST':'example.com'}])
def test_invalid_or_duplicate_ports_fail_before_start(config):
    with pytest.raises(ValueError):listeners(config)


def test_each_port_only_serves_its_assigned_protocol_and_shares_bot():
    config=settings();app=create_app(config);split=SplitPorts(app,18001,18080)
    web=TestClient(split,base_url='http://localhost:18001');bot=TestClient(split,base_url='http://localhost:18080')
    assert web.post('/api/login',json={'password':config.admin_password},headers={'x-mythweaver':'1','origin':'http://localhost:18001'}).status_code==200
    assert bot.get('/api/status').status_code==404 and bot.get('/').status_code==404
    assert bot.post('/api/login',json={'password':config.admin_password}).status_code==404
    assert bot.get('/health').json()=={'ok':True,'service':'onebot'}
    with pytest.raises(WebSocketDisconnect):
        with web.websocket_connect('ws://localhost:18001/channels/onebot/v11/ws',headers={'authorization':'Bearer '+config.onebot_token,'x-self-id':config.bot_id}):pass
    with pytest.raises(WebSocketDisconnect):
        with bot.websocket_connect('ws://localhost:18080/channels/onebot/v11/ws',headers={'authorization':'Bearer wrong','x-self-id':config.bot_id}):pass
    with bot.websocket_connect('ws://localhost:18080/channels/onebot/v11/ws',headers={'authorization':'Bearer '+config.onebot_token,'x-self-id':config.bot_id}) as ws:
        assert app.state.bot.status()['connected']
        ws.send_json({'post_type':'meta_event','self_id':123})
    web.close();bot.close()


def test_real_two_socket_server_auth_and_shutdown_without_database(monkeypatch):
    from websockets.sync.client import connect
    from mythweaver.plugins import Plugins
    from mythweaver.onebot import OneBot
    from mythweaver.db import Store
    calls=[]
    monkeypatch.setattr(Store,'initialize',lambda self:calls.append('initialize'))
    monkeypatch.setattr(Plugins,'reconcile',lambda self:None)
    async def pump(self):
        calls.append('pump')
        await asyncio.Event().wait()
    monkeypatch.setattr(OneBot,'pump',pump)
    config=settings();app=create_app(config)
    sockets=[bind('127.0.0.1',0),bind('127.0.0.1',0)];ports=[s.getsockname()[1] for s in sockets]
    server=uvicorn.Server(uvicorn.Config(SplitPorts(app,*ports),log_level='error'))
    thread=threading.Thread(target=server.run,kwargs={'sockets':sockets},daemon=True);thread.start()
    try:
        deadline=time.monotonic()+5
        while not server.started:
            assert thread.is_alive() and time.monotonic()<deadline
            time.sleep(.01)
        with httpx.Client(trust_env=False) as client:
            assert client.get(f'http://127.0.0.1:{ports[0]}/').status_code==200
            assert client.get(f'http://127.0.0.1:{ports[1]}/health').status_code==200
            assert client.get(f'http://127.0.0.1:{ports[1]}/api/status').status_code==404
        with connect(f'ws://127.0.0.1:{ports[1]}/channels/onebot/v11/ws',additional_headers={'Authorization':'Bearer '+config.onebot_token,'X-Self-ID':config.bot_id}) as ws:
            assert app.state.bot.status()['connected']
            ws.send(json.dumps({'post_type':'meta_event','self_id':123}))
        assert calls==['initialize','pump']
    finally:
        server.should_exit=True;thread.join(6)
        for sock in sockets:sock.close()
    assert not thread.is_alive()
    for port in ports:
        with socket.socket() as sock:assert sock.connect_ex(('127.0.0.1',port))!=0
