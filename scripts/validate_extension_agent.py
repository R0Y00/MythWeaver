"""Live model extension acceptance in an isolated DB schema, with NO OneBot sender."""
# Isolated acceptance explicitly opts into auto approval; production sessions default to manual.
import asyncio
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from sqlalchemy import create_engine,select,text
from mythweaver.config import Settings
from mythweaver.db import Store,ModelProvider,PlatformConfig,Outbox
from mythweaver.harness import Harness
from mythweaver.player import PlayerAgent,view
from mythweaver.plugins import Plugins
from mythweaver.service import Service

REPORT=ROOT/'.runtime/extension-live-report.json'
PROMPT='''请实际创建新的匿名转移玩法，测试通用扩展能力，不仅给出建议。先inspect_runtime读取extensions真实协议。
创建Python插件anonymous_transfer，工具handle，安装测试启用。不得读取宿主文件、数据库或密钥，不调用网络，不发送真实QQ消息。
创建account类型，字段owner字符串、alias字符串、credits非负整数、gm_note字符串。private记录，player_access.owner_field=owner，read_fields仅alias和credits，shared_write=false。
创建两条记录：qq:71001代号白鸦，credits=10，gm_note=ADMIN_ONLY_A；qq:71002代号黑鹿，credits=20，gm_note=ADMIN_ONLY_B。
创建player_enabled的transfer交互，输入只有target_alias字符串、amount正整数。采用extension协议，插件服务端只读取account的owner/alias/credits，scope=game；只可修改credits，scope=game；允许messages。不把其他玩家私有记录或真实QQ提供给玩家模型。
插件按actor找到发起者，按target_alias找到接收者；目标不存在、自转移或余额不足返回空updates/messages，不能扣钱。正常时原子扣发起者amount、加接收者amount，返回两条不同私聊正文：发起者看到转移数量和目标代号；接收者只看到收到匿名转移和数量，不能包含发送者代号、QQ或备注。工具player_enabled=true。
至少两个安装测试：正常10向20转移3得到7/23及不同通知；余额不足时无修改和通知。代码必须根据输入运算，不硬编码真实记录ID。
启用玩家Agent，player_instructions说明可向代号黑鹿匿名转移，金额需明确。不替玩家执行。不要绑定任何QQ；验收程序将在无OneBot发送器的隔离环境绑定虚拟身份。最后读取记录/插件核实。'''


def save(report):REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def isolated(schema):
    assert re.fullmatch(r'mw_live_extensions_[a-f0-9]{12}',schema)
    settings=Settings();store=Store(settings.database_url);store.engine.dispose()
    store.engine=create_engine(settings.database_url,connect_args={'options':f'-csearch_path={schema}'})
    store.session.configure(bind=store.engine)
    service=Service(store);service._plugins=Plugins(service,ROOT/'.runtime/extension-live-plugins')
    return store,service


async def verify(report,store,service):
    game_id=report['game_id'];snap=service.snapshot(game_id)
    records=snap['entities'];checks=report.setdefault('checks',{})
    own=next(r for r in records if r['data'].get('owner')=='qq:71001')
    other=next(r for r in records if r['data'].get('owner')=='qq:71002')
    checks['agent_created_private_accounts']=own['visibility']==other['visibility']=='private'
    checks['no_broad_shared_write']=not snap['game']['definition']['types'][own['type_key']]['player_access'].get('shared_write',False)
    for actor in ('71001','71002'):
        service.command(game_id,'channel.bind',{'group_id':'private-'+actor},'virtual-bind-'+actor)
    # This store has no OneBot pump or socket. Outbox rows are local evidence only.
    player=PlayerAgent(service,Settings())
    result=await player.turn(game_id,'qq:71001','请向黑鹿匿名转移3点额度。','extension-live-transfer')
    report['player_result']=result
    after=service.snapshot(game_id)
    by_id={r['id']:r for r in after['entities']}
    checks['real_player_model_triggered']=result['action'] is not None
    checks['two_record_transaction']=by_id[own['id']]['data']['credits']==7 and by_id[other['id']]['data']['credits']==23
    with store.session() as db:
        deliveries=list(db.scalars(select(Outbox).where(Outbox.status=='queued').order_by(Outbox.group_id)))
        report['deliveries']=[{'recipient':d.group_id,'text':d.text,'status':d.status} for d in deliveries]
        checks['two_distinct_private_deliveries']=len(deliveries)==2 and deliveries[0].group_id!=deliveries[1].group_id and deliveries[0].text!=deliveries[1].text
        recipient=next((d.text for d in deliveries if d.group_id=='private-71002'),'')
        checks['anonymous_recipient_message']=bool(recipient) and all(x not in recipient for x in ['白鸦','71001','ADMIN_ONLY'])
        visible=view(service,db,service.game(db,game_id),'qq:71001')
        encoded=json.dumps(visible,ensure_ascii=False)
        checks['model_view_isolated']=other['id'] not in encoded and 'ADMIN_ONLY' not in encoded and '71002' not in encoded
    duplicate=await player.turn(game_id,'qq:71001','请向黑鹿匿名转移3点额度。','extension-live-transfer')
    with store.session() as db:
        checks['duplicate_no_extra_delivery']=duplicate['id']==result['id'] and len(list(db.scalars(select(Outbox))))==2
    checks['no_real_qq_sender']=True
    report['status']='passed' if all(checks.values()) else 'failed';save(report)
    print(json.dumps({'status':report['status'],'checks':checks},ensure_ascii=False),flush=True)


async def main():
    if REPORT.exists():
        report=json.loads(REPORT.read_text(encoding='utf-8'))
        store,service=isolated(report['schema']);harness=Harness(service,Settings())
        if len(sys.argv)>1 and sys.argv[1]=='verify':
            await verify(report,store,service);return
        task=harness.inspect(report['game_id'],report['task_id'])
        print(json.dumps({'status':task['status'],'step':task['step'],'error_code':task['error_code'],
                          'answer':task['answer'][-1500:], 'tools':[{'name':e['name'],'ok':e['result'].get('ok'),'code':e['result'].get('code')} for e in task['executions']]},ensure_ascii=False));return
    settings=Settings();root=Store(settings.database_url)
    schema='mw_live_extensions_'+uuid4().hex[:12]
    with root.engine.begin() as connection:connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    store,service=isolated(schema);store.initialize()
    with root.session() as db:
        providers=[{c.name:getattr(p,c.name) for c in p.__table__.columns} for p in db.scalars(select(ModelProvider))]
        config=db.get(PlatformConfig,'model_routing');routing={c.name:getattr(config,c.name) for c in config.__table__.columns}
    with store.session.begin() as db:
        for provider in providers:db.add(ModelProvider(**provider))
        db.add(PlatformConfig(**routing))
    game=service.create_game('匿名扩展真实Agent验收','独立schema，无OneBot发送器')
    harness=Harness(service,settings);task_id=harness.create(game['id'],PROMPT,approval_mode='auto')
    report={'schema':schema,'game_id':game['id'],'task_id':task_id,'started_at':datetime.now().isoformat(),'status':'running'}
    save(report);print(json.dumps(report,ensure_ascii=False),flush=True)
    task=await harness.run(game['id'],task_id)
    report['creation']={'status':task['status'],'step':task['step'],'error_code':task['error_code'],
        'answer':task['answer'],'executions':[{'name':e['name'],'result':e['result']} for e in task['executions']]}
    report['status']='created' if task['status']=='answered' else 'creation_failed';save(report)
    if task['status']=='answered':await verify(report,store,service)
    else:print(json.dumps({'status':task['status'],'error':task['error_code']},ensure_ascii=False),flush=True)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(main())
