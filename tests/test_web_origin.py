import pytest
from fastapi.testclient import TestClient

from mythweaver.api import create_app
from mythweaver.config import Settings


def client(base,configured='auto'):
    settings=Settings(admin_password='test-login-password',session_secret='x'*64,onebot_token='y'*64,bot_id='123',public_origin=configured)
    # No lifespan or database routes: the platform/database must remain stopped.
    return TestClient(create_app(settings),base_url=base),settings


@pytest.mark.parametrize('base',['http://127.0.0.1:8001','http://192.168.1.100:8001','http://203.0.113.10:28080','http://game.example:28080','https://game.example','https://game.example:8443'])
def test_any_same_origin_address_can_log_in_without_registered_domain(base):
    api,settings=client(base)
    response=api.post('/api/login',json={'password':settings.admin_password},headers={'x-mythweaver':'1','origin':base})
    assert response.status_code==200
    assert ('; Secure' in response.headers['set-cookie'])==base.startswith('https:')
    assert api.post('/api/logout',headers={'x-mythweaver':'1','origin':base}).status_code==200
    api.close()


@pytest.mark.parametrize('configured',['','*','http://0.0.0.0:8001'])
def test_auto_and_old_bind_address_placeholder_work(configured):
    api,settings=client('http://game.example:28080',configured)
    assert api.post('/api/login',json={'password':settings.admin_password},headers={'x-mythweaver':'1','origin':'http://game.example:28080'}).status_code==200
    api.close()


@pytest.mark.parametrize('origin',['http://evil.example','null','https://game.example:8001','http://game.example:8002','http://game.example:8001/path'])
def test_other_sites_remain_rejected(origin):
    api,settings=client('http://game.example:8001')
    assert api.post('/api/login',json={'password':settings.admin_password},headers={'x-mythweaver':'1','origin':origin}).status_code==403
    api.close()


def test_password_and_request_header_checks_are_preserved():
    api,settings=client('http://game.example:8001')
    assert api.post('/api/login',json={'password':settings.admin_password},headers={'origin':'http://game.example:8001'}).status_code==403
    assert api.post('/api/login',json={'password':'wrong'},headers={'x-mythweaver':'1','origin':'http://game.example:8001'}).status_code==401
    api.close()


def test_optional_explicit_origin_still_restricts_access():
    api,settings=client('http://local.example:8001','http://fixed.example:8001')
    headers={'x-mythweaver':'1','origin':'http://local.example:8001'}
    assert api.post('/api/login',json={'password':settings.admin_password},headers=headers).status_code==403
    headers['origin']='http://fixed.example:8001'
    assert api.post('/api/login',json={'password':settings.admin_password},headers=headers).status_code==200
    api.close()
