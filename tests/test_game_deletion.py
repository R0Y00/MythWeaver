import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from mythweaver.api import create_app
from mythweaver.config import Settings
from mythweaver.db import AgentRun,AgentSession,Base,Game,Inbox,Outbox,PlatformConfig,SystemLog,ToolExecution
from mythweaver.definitions import DomainError
from mythweaver.game_lifecycle import delete_game
from mythweaver.harness import Harness


def test_deletion_removes_dependencies_and_preserves_other_games(service,game):
    other=service.create_game('保留空间','')
    harness=Harness(service,Settings());rid=harness.create(game['id'],'等待中的会话')
    with service.store.session.begin() as db:
        run=db.get(AgentRun,rid);run.status='awaiting_approval';sid=run.session_id
        db.add(ToolExecution(run_id=rid,call_id='pending',name='execute_commands',arguments={},status='awaiting_approval'))
        db.add(Inbox(key='in-delete',game_id=game['id'],actor='qq:1',content='secret'))
        db.add(Outbox(game_id=game['id'],group_id='123',text='secret'))
        db.add(PlatformConfig(key='deletion-test-global',value={'preserve':True}))
    service.command(game['id'],'channel.bind',{'group_id':'123'},'bind')
    service.command(game['id'],'submit',{'interaction':'_free','data':{'text':'secret'}},'entry')
    service.command(other['id'],'submit',{'interaction':'_free','data':{'text':'keep'}},'entry')
    current=service.snapshot(game['id'])['game']
    result=delete_game(service,game['id'],current['version'],current['name'])
    assert result['status']=='deleted' and result['deleted_records']['tool_executions']==1
    assert len(service.snapshot(other['id'])['submissions'])==1
    with service.store.session() as db:
        assert db.get(Game,game['id']) is None
        assert db.get(AgentSession,sid) is None
        for table in Base.metadata.sorted_tables:
            if 'game_id' in table.c:assert db.execute(select(table.c.game_id).where(table.c.game_id==game['id'])).first() is None
        assert db.get(PlatformConfig,'deletion-test-global').value=={'preserve':True}
        assert db.scalar(select(SystemLog).where(SystemLog.kind=='game.deleted')).detail['game_id']==game['id']
    with pytest.raises(DomainError):service.snapshot(game['id'])


@pytest.mark.parametrize('model,status',[(AgentRun,'running'),(Outbox,'sending'),(Inbox,'processing')])
def test_busy_game_is_not_deleted(service,game,model,status):
    with service.store.session.begin() as db:
        if model==AgentRun:db.add(model(game_id=game['id'],prompt='active',status=status))
        elif model==Outbox:db.add(model(game_id=game['id'],group_id='123',text='active',status=status))
        else:db.add(model(game_id=game['id'],key='active',actor='qq:1',content='active',status=status))
    with pytest.raises(DomainError) as error:delete_game(service,game['id'],game['version'],game['name'])
    assert error.value.code=='GAME_BUSY'
    assert service.snapshot(game['id'])['game']['id']==game['id']


def test_http_deletion_requires_auth_name_and_version(store,service,game):
    settings=Settings()
    with TestClient(create_app(settings,store)) as client:
        client.headers['X-MythWeaver']='1';path='/api/games/'+game['id']
        body={'version':game['version'],'confirmation':game['name']}
        assert client.request('DELETE',path,json=body).status_code==401
        client.post('/api/login',json={'password':settings.admin_password}).raise_for_status()
        assert client.request('DELETE',path,json={**body,'confirmation':'wrong'}).json()['code']=='CONFIRMATION_REQUIRED'
        assert client.request('DELETE',path,json={**body,'version':999}).json()['code']=='STATE_STALE'
        assert client.request('DELETE',path,json=body).status_code==200
        assert client.get(path).status_code==404
        assert client.get('/api/games').json()==[]
        assert client.request('DELETE',path,json=body).status_code==404


def test_deletion_serializes_with_message_claim(service,game):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event,current_thread
    from sqlalchemy import event
    from mythweaver.onebot import OneBot
    service.command(game['id'],'channel.bind',{'group_id':'123'},'bind')
    with service.store.session.begin() as db:db.add(Outbox(game_id=game['id'],group_id='123',text='must not escape'))
    deleting=Event();claiming=Event();release=Event()
    def intercept(connection,cursor,statement,parameters,context,many):
        sql=statement.lower()
        if 'delete from outbox' in sql:
            deleting.set()
            if not release.wait(5):raise RuntimeError('deletion race test timed out')
        if 'delivery' in current_thread().name and 'from games' in sql and 'for update' in sql:claiming.set()
    event.listen(service.store.engine,'before_cursor_execute',intercept)
    try:
        with ThreadPoolExecutor(max_workers=1,thread_name_prefix='deletion') as deletes,ThreadPoolExecutor(max_workers=1,thread_name_prefix='delivery') as deliveries:
            removal=deletes.submit(delete_game,service,game['id'],game['version'],game['name'])
            try:
                assert deleting.wait(3)
                sending=deliveries.submit(OneBot(service.store,service,Settings()).next_delivery)
                assert claiming.wait(3)
                assert not sending.done()
            finally:release.set()
            assert removal.result(5)['status']=='deleted'
            assert sending.result(5) is None
    finally:
        release.set();event.remove(service.store.engine,'before_cursor_execute',intercept)
