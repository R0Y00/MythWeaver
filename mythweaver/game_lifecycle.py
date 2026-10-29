"""Explicit administrator deletion, scoped to one game's relational records."""
from sqlalchemy import delete,select

from .db import AgentRun,Base,Game,Inbox,Outbox,PlayerTurn,PluginRelease,SystemLog,ToolExecution
from .definitions import DomainError


def delete_game(service,game_id,version,confirmation,execution_id=None):
    with service.store.session.begin() as db:
        game=service.game(db,game_id,lock=True)
        if game.version!=version:raise DomainError('STATE_STALE','游戏已变化，请刷新后重新确认删除',409)
        if confirmation!=game.name:raise DomainError('CONFIRMATION_REQUIRED','请输入完整游戏名称确认删除',409)
        for model,statuses in ((AgentRun,['running']),(PlayerTurn,['running']),(Inbox,['processing']),
                               (Outbox,['sending']),(PluginRelease,['installing','testing'])):
            if db.scalar(select(model).where(model.game_id==game_id,model.status.in_(statuses)).limit(1)):
                raise DomainError('GAME_BUSY','游戏仍有任务执行或消息发送中，请先停止或等待完成再删除',409)
        cross=select(ToolExecution.id).where(ToolExecution.name.in_(['workspace_call','workspace_delete']),
                ToolExecution.arguments['game_id'].astext==game_id,ToolExecution.status=='running')
        if execution_id:cross=cross.where(ToolExecution.id!=execution_id)
        if db.scalar(cross.limit(1)):
            raise DomainError('GAME_BUSY','另一个会话正在操作此游戏，请等待该操作完成',409)
        # Delete dependent receipts before runs; all remaining game children follow FK order.
        run_ids=select(AgentRun.id).where(AgentRun.game_id==game_id)
        counts={'tool_executions':db.execute(delete(ToolExecution).where(ToolExecution.run_id.in_(run_ids))).rowcount}
        for table in reversed(Base.metadata.sorted_tables):
            if 'game_id' in table.c:
                counts[table.name]=db.execute(delete(table).where(table.c.game_id==game_id)).rowcount
        db.execute(delete(Game).where(Game.id==game_id))
        db.add(SystemLog(kind='game.deleted',detail={'game_id':game_id,'name':game.name,'deleted_records':counts}))
        return {'id':game_id,'name':game.name,'status':'deleted','deleted_records':counts}
