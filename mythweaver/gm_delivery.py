"""Explicit private delivery of decisions; never fall back to a public group."""
from .db import Channel,Outbox
from .definitions import DomainError
from .policy import policy_for,check_participant


def enqueue(service,db,game,submission):
    setting=game.definition.get('interactions',{}).get(submission.interaction,{}).get('gm_delivery','none')
    if submission.interaction=='_review':setting=policy_for(db,game.id)[0].get('gm_delivery','none')
    if setting!='private' or not submission.actor.startswith('qq:'):return None
    target='private-'+submission.actor[3:]
    channel=db.get(Channel,target)
    if not channel or channel.game_id!=game.id:
        raise DomainError('DELIVERY_UNBOUND','该玩家未绑定本游戏私聊。请先绑定，或关闭自动通知后保存裁决。',409)
    check_participant(db,game.id,submission.actor)
    message=Outbox(game_id=game.id,group_id=target,text=('GM裁决：\n'+submission.result)[:8000])
    db.add(message);db.flush()
    service.event(db,game.id,'admin','decision.delivery',{'submission_id':submission.id,'delivery_id':message.id,'recipient':submission.actor})
    return message.id
