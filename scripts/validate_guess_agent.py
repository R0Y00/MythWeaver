"""Run the user's guess-game request through the real model, without a QQ sender."""
# Isolated acceptance explicitly opts into auto approval; production sessions default to manual.
import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from sqlalchemy import create_engine,select,text
from mythweaver.config import Settings
from mythweaver.db import Store,ModelProvider,PlatformConfig
from mythweaver.harness import Harness
from mythweaver.plugins import Plugins
from mythweaver.service import Service

REPORT=ROOT/('.runtime/guess-fresh-live-report.json' if '--fresh' in sys.argv else '.runtime/guess-live-report.json')
PROMPT='来一个猜数字游戏，用户提范围然后你说对不对，最后猜到了就结束。请实际创建成玩家可以玩的玩法，并测试，不只给建议。数字范围由玩家指定，目标数字应随机且对玩家隐藏，反馈偏大/偏小/猜中，结束后不能继续结算。不要发送真实QQ消息或绑定真实账号。'

def save(report):REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


async def verify(report,store,service):
    from mythweaver.player import PlayerAgent,view
    from mythweaver.db import Outbox
    from copy import deepcopy
    game_id=report['game_id'];player=PlayerAgent(service,Settings());checks={};turns=[]
    started=await player.turn(game_id,'qq:10001','重新开启一局猜数字，范围从1到10，清空旧局，提交开启操作。','guess-live-start-'+uuid4().hex)
    turns.append(started)
    checks['natural_language_start']=bool(started['action']) and started['action']['interaction']=='guess_start'
    if not checks['natural_language_start']:
        report['player_acceptance']={'checks':checks,'turns':turns,'passed':False};save(report)
        print(json.dumps({'passed':False,'checks':checks,'answer':started['answer']},ensure_ascii=False),flush=True);return
    records=service.snapshot(game_id)['entities'];record=next(r for r in records if 'target' in r['data'] and 'attempts' in r['data']);target=record['data']['target']
    checks['target_in_requested_range']=1<=target<=10 and record['data']['status']=='playing'
    with store.session() as db:
        visible=view(service,db,service.game(db,game_id),'qq:10001')
        checks['hidden_answer_not_visible']=all('target' not in r['data'] and 'winner' not in r['data'] for r in visible['records'])
    wrong=target-1 if target>1 else target+1
    guessed=await player.turn(game_id,'qq:10001',f'我猜{wrong}，提交这次猜测。','guess-live-wrong-'+uuid4().hex);turns.append(guessed)
    checks['actual_wrong_guess_feedback']=('偏小' if wrong<target else '偏大') in guessed['answer'] and bool(guessed['action'])
    won=await player.turn(game_id,'qq:10001',f'我猜{target}，提交这次猜测。','guess-live-win-'+uuid4().hex);turns.append(won)
    state=next(r for r in service.snapshot(game_id)['entities'] if r['id']==record['id'])['data']
    checks['win_feedback_and_persistent_end']='猜中' in won['answer'] and state['status']=='won'
    before=deepcopy(state)
    stopped=await player.turn(game_id,'qq:10001',f'还是同一局，再提交猜测{wrong}，不要开启新局。','guess-live-ended-'+uuid4().hex);turns.append(stopped)
    after=next(r for r in service.snapshot(game_id)['entities'] if r['id']==record['id'])['data']
    checks['ended_game_no_more_scoring']=after['status']=='won' and after['attempts']==before['attempts'] and after.get('history')==before.get('history')
    with store.session() as db:checks['no_qq_deliveries']=not list(db.scalars(select(Outbox)))
    report['player_acceptance']={'checks':checks,'turns':turns,'passed':all(checks.values())};save(report)
    print(json.dumps({'passed':all(checks.values()),'checks':checks,'answers':[t['answer'] for t in turns]},ensure_ascii=False),flush=True)

async def main():
    settings=Settings();root=Store(settings.database_url)
    if any(flag in sys.argv for flag in ('--resume','--verify')) and REPORT.exists():
        report=json.loads(REPORT.read_text(encoding='utf-8'))
        store=Store(settings.database_url);store.engine.dispose()
        store.engine=create_engine(settings.database_url,connect_args={'options':f"-csearch_path={report['schema']}"})
        store.session.configure(bind=store.engine)
        service=Service(store);service._plugins=Plugins(service,ROOT/'.runtime/guess-live-plugins')
        if '--verify' in sys.argv:
            await verify(report,store,service);return
        store.initialize()
        from mythweaver.db import AgentSession,AgentRun
        with store.session.begin() as db:
            run=db.get(AgentRun,report['task_id'])
            if run.session_id:db.get(AgentSession,run.session_id).approval_mode='auto'
        harness=Harness(service,settings)
        harness.resume(report['game_id'],report['task_id'],'继续完成此前猜数字任务。平台刚新增extension返回feedback，直接提供本次行动的结果文本给行动者，无需绑定QQ；请检查inspect_runtime新协议。更新插件start/judge，将原来last_feedback的文本同时作为feedback返回，补测试并启用。用simulate_actions批量试玩并验证偏大/偏小/猜中/结束后不结算，避免再次逐个提交测试。核实玩家Agent已启用，公开读取字段不包含target，然后给出中文玩家使用例子及真实QQ尚未绑定的限制，不发送真实消息。')
        result=await harness.run(report['game_id'],report['task_id'])
        report['creation']=result;report['snapshot']=service.snapshot(report['game_id']);report['status']=result['status'];save(report)
        print(json.dumps({'status':result['status'],'step':result['step'],'error_code':result['error_code'],'answer':result['answer'][-2500:]},ensure_ascii=False),flush=True)
        return
    schema='mw_live_guess_'+uuid4().hex[:12]
    with root.engine.begin() as connection:connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    store=Store(settings.database_url);store.engine.dispose()
    store.engine=create_engine(settings.database_url,connect_args={'options':f'-csearch_path={schema}'})
    store.session.configure(bind=store.engine);store.initialize()
    with root.session() as db:
        providers=[{c.name:getattr(p,c.name) for c in p.__table__.columns} for p in db.scalars(select(ModelProvider))]
        config=db.get(PlatformConfig,'model_routing')
        routing={c.name:getattr(config,c.name) for c in config.__table__.columns} if config else None
    with store.session.begin() as db:
        for provider in providers:db.add(ModelProvider(**provider))
        if routing:db.add(PlatformConfig(**routing))
    service=Service(store);service._plugins=Plugins(service,ROOT/'.runtime/guess-live-plugins')
    game=service.create_game('猜数字真实模型验收','独立schema，无QQ发送器')
    harness=Harness(service,settings);rid=harness.create(game['id'],PROMPT,approval_mode='auto')
    report={'schema':schema,'game_id':game['id'],'task_id':rid,'started_at':datetime.now().isoformat(),'status':'running'};save(report)
    print(json.dumps(report,ensure_ascii=False),flush=True)
    result=await harness.run(game['id'],rid)
    report['creation']=result;report['snapshot']=service.snapshot(game['id']);report['status']=result['status'];save(report)
    print(json.dumps({'status':result['status'],'step':result['step'],'error_code':result['error_code'],'answer':result['answer'][-2500:]},ensure_ascii=False),flush=True)

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');asyncio.run(main())
