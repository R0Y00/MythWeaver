"""Persist approval before an effect, bound to its exact arguments and state."""
import hashlib
import json
from typing import Literal

from sqlalchemy import select

from .db import AgentRun,AgentSession,Game,GamePolicy,ToolExecution,utcnow
from .definitions import DomainError
from .schemas import Input

READ_ONLY={'query_records','query_events','inspect_flow','inspect_runtime','inspect_capabilities','list_controls','validate_module',
           'plugin_list','plugin_inspect','record_progress','finish_task','platform_rules_read','workspace_list','workspace_inspect'}
READ_ONLY.update({'requirements_read','trial_list'})
READ_ONLY.add('inspect_player_agent')
READ_ONLY.add('ask_admin')

class ApprovalDecision(Input):
    decision:Literal['approve','reject']
    reason:str=''

def stamp(service,db,game_id,name,arguments=None):
    from .agent import fingerprint
    from .platform_rules import read
    state=[fingerprint(db,service.game(db,game_id)),read(db)['version']]
    if name in ('workspace_call','workspace_delete'):
        target=(arguments or {})['game_id']
        state.append([target,fingerprint(db,service.game(db,target))])
    if name=='platform_rules_update':
        state.append(list(db.execute(select(Game.id,Game.version).order_by(Game.id)).all()))
        state.append(list(db.execute(select(GamePolicy.game_id,GamePolicy.version).order_by(GamePolicy.game_id)).all()))
    return hashlib.sha256(json.dumps(state,default=list).encode()).hexdigest()

def summary(name,arguments):
    if name=='workspace_create':return '创建新的空白游戏：'+arguments['name']
    if name=='workspace_delete':return '永久删除游戏“'+arguments['confirmation']+'”及其数据、会话、插件版本记录、检查点和 QQ 绑定（无法撤销）'
    if name=='workspace_call':return '操作目标游戏 '+arguments['game_id']+'：'+summary(arguments['tool'],arguments['arguments'])
    if name=='execute_commands':return arguments.get('explanation','修改游戏数据')[:1000]
    if name=='platform_rules_update':return '修改全平台运行规则'+('，同时覆盖各游戏对应设置' if arguments.get('apply_scope')=='all_games' else '（保留游戏单独覆盖）')
    external='；允许运行可信插件，文件和网络副作用不能回滚' if arguments.get('allow_external') else '；默认禁止宿主插件，不发送QQ'
    if name=='acceptance_run':return '运行业务说明第 '+str(arguments['revision'])+' 版的全部验收场景（正式数据库变化最终回滚）'+external
    if name=='trial_create':return '创建持续隔离试玩：'+arguments.get('label','隔离试玩')+external
    if name=='trial_turn':return '在隔离试玩中以 '+arguments['actor']+' 的身份处理一条消息；正式数据不改变，插件权限沿用该试玩空间设置'
    if name=='trial_change':return ('重置到初始快照：' if arguments['action']=='reset' else '丢弃隔离试玩：')+arguments['id']+'；不修改正式游戏'
    return {'plugin_draft':'保存代码插件源码','plugin_install':'安装插件依赖、运行测试并按请求启用',
            'plugin_state':'调整插件启用状态','simulate_actions':'运行代码进行连续试玩（数据库最终回滚）',
            'simulate_conversation':'调用真实玩家模型进行'+arguments.get('audience','')+'连续聊天测试（数据库最终回滚）'}.get(name,'运行已安装插件工具：'+name)

def authorize(harness,game_id,run_id,execution_id):
    """Return False only when execution must pause; raise on rejected/stale requests."""
    with harness.store.session.begin() as db:
        pending=db.get(ToolExecution,execution_id)
        ids={game_id}
        if pending and pending.name in ('workspace_call','workspace_delete'):ids.add(pending.arguments['game_id'])
        games=list(db.scalars(select(Game).where(Game.id.in_(ids)).order_by(Game.id).with_for_update()))
        if len(games)!=len(ids):raise DomainError('NOT_FOUND','会话或目标游戏不存在',404)
        row=db.scalar(select(ToolExecution).where(ToolExecution.id==execution_id,ToolExecution.run_id==run_id).with_for_update())
        run=db.get(AgentRun,run_id);session=db.get(AgentSession,run.session_id) if run.session_id else None
        if row.name in READ_ONLY or (row.name=='workspace_call' and row.arguments.get('tool') in READ_ONLY):return True
        approval=row.approval or {}
        if approval.get('decision')=='reject':raise DomainError('ADMIN_REJECTED','管理员拒绝此操作：'+approval.get('reason','请继续讨论或调整方案'),409)
        if approval.get('decision')=='approve':
            if approval['state']!=stamp(harness.service,db,game_id,row.name,row.arguments):
                raise DomainError('APPROVAL_STALE','批准后平台或游戏状态已变化，请重新查询并提出新的操作请求',409)
            row.status='running';return True
        if row.status=='awaiting_approval':
            run.status='awaiting_approval';return False
        if session and session.approval_mode=='auto':
            row.approval={'decision':'auto','scope':'session','at':utcnow().isoformat()};return True
        row.status='awaiting_approval'
        row.approval={'decision':'pending','state':stamp(harness.service,db,game_id,row.name,row.arguments),
                      'summary':summary(row.name,row.arguments),'requested_at':utcnow().isoformat()}
        if row.name in ('workspace_call','workspace_delete'):
            target=harness.service.game(db,row.arguments['game_id'])
            row.approval={**row.approval,'target_game':{'id':target.id,'name':target.name}}
        run.status='awaiting_approval'
        run.answer='需要你批准：'+row.approval['summary']+'。你也可以直接补充要求，我会调整方案。'
        return False

def decide(harness,game_id,run_id,execution_id,decision,reason=''):
    if decision not in ('approve','reject') or len(reason)>2000:raise DomainError('INVALID_INPUT','审批选择或说明无效')
    with harness.store.session.begin() as db:
        harness.service.game(db,game_id,lock=True)
        run=db.get(AgentRun,run_id)
        row=db.scalar(select(ToolExecution).where(ToolExecution.id==execution_id,ToolExecution.run_id==run_id).with_for_update())
        if not run or run.game_id!=game_id or not row:raise DomainError('NOT_FOUND','操作请求不存在',404)
        if run.status!='awaiting_approval' or row.status!='awaiting_approval':raise DomainError('STATE_STALE','此请求已处理或已被新消息取代',409)
        # Check again inside authorize immediately before the effect. This decision never executes code itself.
        row.approval={**row.approval,'decision':decision,'reason':reason,'decided_at':utcnow().isoformat()}
        row.status='approved' if decision=='approve' else 'rejected'
        run.status='running';run.answer=''
