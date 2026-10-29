from sqlalchemy import select

from .db import GamePolicy, Participant
from .definitions import DomainError

DEFAULT_POLICY = {"paused":False,"command_prefix":"/mw","free_mode":"manual","allow_users":[],"instructions":"","gm_delivery":"none",
                  "player_agent_enabled":False,"player_instructions":""}


def policy_for(db,game_id):
    row = db.get(GamePolicy,game_id)
    from .platform_rules import effective
    inherited=effective(db,game_id)
    defaults={k:v for k,v in inherited.items() if k in ('command_prefix','prefix_requires_space','private_trigger','group_trigger','group_input')}
    return {**DEFAULT_POLICY,**defaults,**{k:v for k,v in (row.config if row else {}).items() if v is not None}}, row.version if row else 0


def check_participant(db,game_id,actor):
    policy,_ = policy_for(db,game_id)
    if actor == "admin":
        return
    if policy["paused"]:
        raise DomainError("GAME_PAUSED","管理员已暂停新提交，已有内容保留",409)
    if policy["allow_users"] and actor not in policy["allow_users"]:
        raise DomainError("PARTICIPATION_DENIED","你尚未获得这个游戏的参与权限",403)
    participant = db.scalar(select(Participant).where(Participant.game_id==game_id,Participant.actor==actor))
    if participant and participant.blocked:
        raise DomainError("PARTICIPATION_DENIED","你的参与权限已暂停",403)

