from uuid import uuid4

from mythweaver.examples import instantiate


def run(service,game,kind,data):
    return service.command(game['id'],kind,data,uuid4().hex)


def test_proposal_configuration_automatic_and_manual_share_same_core(service):
    game=instantiate(service,'proposal-collaboration')
    snapshot=service.snapshot(game['id'])
    targets=[e for e in snapshot['entities'] if e['type_key']=='proposal']
    run(service,game,'submit',{'interaction':'request','data':{'target':targets[0]['id'],'requested':5}})
    run(service,game,'submit',{'interaction':'request','data':{'target':targets[1]['id'],'requested':50}})
    snapshot=service.snapshot(game['id'])
    waiting=next(r for r in snapshot['flows'] if r['status']=='waiting')
    assert len([r for r in snapshot['flows'] if r['status']=='completed'])==1
    run(service,game,'flow.resume',{'id':waiting['id'],'version':1,'data':{'approved':True,'amount':32},'reason':'管理员修改额度'})
    assert next(e for e in service.snapshot(game['id'])['entities'] if e['id']==targets[1]['id'])['data']['allocation']==32
    assert snapshot['game']['definition']['types'].keys()=={'proposal'}


def test_investigation_configuration_has_admin_defined_development(service):
    game=instantiate(service,'city-investigation')
    target=next(e for e in service.snapshot(game['id'])['entities'] if e['type_key']=='lead')
    run(service,game,'submit',{'interaction':'investigate','data':{'target':target['id']}})
    waiting=service.snapshot(game['id'])['flows'][0]
    before=next(e for e in service.snapshot(game['id'])['entities'] if e['id']==target['id'])
    assert before['data']['development']=='' and before['data']['state']=='调查中'
    run(service,game,'flow.resume',{'id':waiting['id'],'version':1,'data':{'route':'继续追踪','development':'管理员决定：档案另有副本。'},'reason':'选择后续发展'})
    after=next(e for e in service.snapshot(game['id'])['entities'] if e['id']==target['id'])
    assert after['data']['state']=='继续追踪' and '副本' in after['data']['development']
    assert service.snapshot(game['id'])['flows'][0]['status']=='completed'
