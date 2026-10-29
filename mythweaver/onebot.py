"""Direct OneBot v11 transport. No chatbot framework, no implicit game rules."""
import asyncio
import json
import logging
import secrets
import time

from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from .db import Channel, Game, Inbox, Outbox
from .definitions import DomainError
from .policy import policy_for, check_participant

log = logging.getLogger(__name__)


class OneBot:
    def __init__(self, store, service, settings):
        self.store, self.service, self.settings = store, service, settings
        self.socket: WebSocket | None = None
        self.generation = 0
        self.pending = {}
        self.last_seen = None
        self.last_received = None
        self.last_sent = 0.0
        from .player import PlayerAgent
        self.player_agent=PlayerAgent(service,settings)
        self.player_tasks={}

    def status(self):
        return {"connected": self.socket is not None, "bot_id": self.settings.bot_id,
                "generation": self.generation, "last_seen": self.last_seen,
                "pending_calls": len(self.pending)}

    async def connection(self, ws):
        auth = ws.headers.get("authorization", "")
        bot = ws.headers.get("x-self-id", "")
        if not secrets.compare_digest(auth, f"Bearer {self.settings.onebot_token}") or bot != self.settings.bot_id:
            await ws.close(code=1008)
            return
        await ws.accept()
        previous = self.socket
        self.generation += 1
        generation = self.generation
        self.socket = ws
        self.last_received = time.monotonic()
        if previous:
            for (old_generation, _), future in list(self.pending.items()):
                if old_generation != generation and not future.done():
                    future.set_exception(ConnectionError("session replaced"))
            await previous.close(code=1012)
        try:
            while True:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=90)
                if len(raw) > 65536:
                    await ws.close(code=1009)
                    break
                try:
                    frame = json.loads(raw)
                    if not isinstance(frame, dict):
                        continue
                    if generation != self.generation:
                        break
                    if "post_type" in frame:
                        if str(frame.get("self_id", "")) != self.settings.bot_id:
                            await ws.close(code=1008)
                            break
                        if frame.get("post_type") == "message":
                            await asyncio.to_thread(self.accept_event, frame)
                    elif "echo" in frame:
                        future = self.pending.get((generation, str(frame["echo"])))
                        if future and not future.done():
                            future.set_result(frame)
                    self.last_seen = time.time()
                    self.last_received = time.monotonic()
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
        except Exception as exc:
            # No raw frames, credentials, or private content in logs.
            log.info("OneBot session ended: %s", type(exc).__name__)
        finally:
            if generation == self.generation:
                self.socket = None
            for (g, _), future in list(self.pending.items()):
                if g == generation and not future.done():
                    future.set_exception(ConnectionError("disconnected"))

    async def call(self, action, params, timeout=15):
        ws, generation = self.socket, self.generation
        if not ws:
            raise ConnectionError("offline")
        echo = secrets.token_hex(16)
        future = asyncio.get_running_loop().create_future()
        self.pending[generation, echo] = future
        try:
            await ws.send_json({"action": action, "params": params, "echo": echo})
            result = await asyncio.wait_for(future, timeout)
            if result.get("status") != "ok" or result.get("retcode") != 0:
                raise ValueError("OneBot rejected request")
            return result.get("data")
        finally:
            self.pending.pop((generation, echo), None)

    def accept_event(self, frame):
        private=frame.get('message_type')=='private'
        if frame.get("message_type") not in ('group','private') or str(frame.get("user_id")) == self.settings.bot_id:
            return
        user=str(frame.get('user_id',''))
        if not user.isdecimal() or len(user)>25:
            return
        group = 'private-'+user if private else str(frame.get("group_id", ""))
        message_id = frame.get("message_id")
        segments = frame.get("message")
        if (not private and not group.isdecimal()) or message_id is None or not isinstance(segments, list):
            return
        text = "".join(s.get("data", {}).get("text", "") for s in segments
                       if isinstance(s, dict) and s.get("type") == "text").strip()
        # Only bound conversations matching administrator-configured triggers enter storage.
        if len(text) > 16000:
            return
        with self.store.session.begin() as db:
            channel = db.get(Channel, group)
            if not channel:
                return
            from .platform_rules import effective,extract
            rules=effective(db,channel.game_id)
            if private and not policy_for(db,channel.game_id)[0]['player_agent_enabled']:
                return
            mentioned=any(isinstance(s,dict) and s.get('type')=='at' and str(s.get('data',{}).get('qq'))==self.settings.bot_id for s in segments)
            content=extract(text,rules,private,mentioned)
            if content is None:return
            key = f"{self.settings.bot_id}:{group}:{message_id}"
            db.execute(insert(Inbox).values(key=key, game_id=channel.game_id,
                       actor=f"qq:{frame['user_id']}", content=content,route='agent' if private or rules['group_input']=='agent' else 'commands').on_conflict_do_nothing())

    def process_inbox(self):
        with self.store.session.begin() as db:
            candidate=db.scalar(select(Inbox).where(Inbox.status=='queued',Inbox.route!='agent',~Inbox.key.like('%:private-%')).order_by(Inbox.created_at).limit(1))
            if not candidate:
                return
            # Same lock order as management/restoration: game first, then inbox.
            self.service.game(db,candidate.game_id,lock=True)
            row=db.scalar(select(Inbox).where(Inbox.key==candidate.key,Inbox.status=='queued').with_for_update(skip_locked=True))
            if not row:
                return
            group = row.key.split(":")[1]
            channel = db.get(Channel, group)
            if not channel or channel.game_id != row.game_id:
                row.status = "cancelled"
                return
            text = row.content
            prefix = policy_for(db,row.game_id)[0]["command_prefix"]
            try:
                # A savepoint lets us record an error without committing partial changes.
                with db.begin_nested():
                    game = self.service.game(db, row.game_id)
                    check_participant(db,row.game_id,row.actor)
                    prefix = policy_for(db,row.game_id)[0]["command_prefix"]
                    if text == "帮助":
                        interactions = [f"{key}（{spec.get('label', key)}）" for key, spec in game.definition.get("interactions", {}).items() if spec.get("enabled", True) and (not spec.get("allow_users") or row.actor in spec["allow_users"])]
                        reply = f"MythWeaver：{prefix} 提交 内容；{prefix} 使用 标识 JSON。可用交互：" + ("、".join(interactions) or "尚未定义，支持自由提交")
                    else:
                        if text.startswith("使用 "):
                            parts = text[3:].split(" ", 1)
                            if len(parts) != 2:
                                raise DomainError("INVALID_INPUT", f'格式：{prefix} 使用 标识 {{"字段":"内容"}}')
                            payload = {"interaction": parts[0], "data": json.loads(parts[1])}
                        elif text.startswith("提交 "):
                            payload = {"interaction": "_free", "data": {"text": text[3:]}}
                        else:
                            raise DomainError("INVALID_INPUT", f"使用 {prefix} 帮助 查看操作")
                        result = self.service.execute(db, row.game_id, row.actor, row.key, "submit", payload)
                        reply = f"已受理 #{result['id'][:8]} · " + ("等待管理员决定" if result["status"] == "pending" or result.get('flow_status')=='waiting' else "已处理")
                        # Fixed response is explicitly public in the interaction definition.
                        if result.get("result"):
                            reply += "\n" + result["result"]
                row.status = "processed"
            except DomainError as exc:
                row.status = "rejected"
                reply = exc.message
            except (json.JSONDecodeError, TypeError, KeyError):
                row.status = "rejected"
                reply = f"输入格式无效，请使用 {prefix} 帮助"
            db.add(Outbox(group_id=group, game_id=row.game_id, text=reply[:8000]))

    def claim_private(self):
        with self.store.session.begin() as db:
            from sqlalchemy import or_
            candidate=db.scalar(select(Inbox).where(Inbox.status=='queued',or_(Inbox.route=='agent',Inbox.key.like('%:private-%'))).order_by(Inbox.created_at).limit(1))
            if not candidate:
                return None
            self.service.game(db,candidate.game_id,lock=True)
            row=db.scalar(select(Inbox).where(Inbox.key==candidate.key,Inbox.status=='queued').with_for_update(skip_locked=True))
            if not row:
                return None
            target=row.key.split(':')[1]
            channel=db.get(Channel,target)
            from .platform_rules import effective
            rules=effective(db,row.game_id)
            if (not channel or channel.game_id!=row.game_id or
                (target.startswith('private-') and (target!='private-'+row.actor.removeprefix('qq:') or rules['private_trigger']=='disabled')) or
                (not target.startswith('private-') and (rules['group_input']!='agent' or rules['group_trigger']=='disabled'))):
                row.status='cancelled'
                return None
            row.status='processing'
            return row.key,row.game_id,row.actor,row.content,target

    async def process_private(self,item):
        from .player import private_reply
        import hashlib
        key,game_id,actor,prompt,target=item
        try:
            if target.startswith('private-'):
                result=await self.player_agent.turn(game_id,actor,prompt,hashlib.sha256(key.encode()).hexdigest())
                reply=private_reply(result)
            else:
                result=await self.player_agent.turn(game_id,actor,prompt,hashlib.sha256(key.encode()).hexdigest(),audience='group',conversation_id=target)
                reply=result['answer']
            status='processed'
        except DomainError as exc:
            reply,status=exc.message,'rejected'
        except Exception:
            reply,status='玩家助手处理失败，请管理员查看连接和模型设置。','rejected'
        with self.store.session.begin() as db:
            game=self.service.game(db,game_id,lock=True)
            row=db.get(Inbox,key)
            channel=db.get(Channel,target)
            if row.status!='processing' or not channel or channel.game_id!=game_id:
                row.status='cancelled'
                return
            if status=='processed':
                from .agent import fingerprint
                if result['state_stamp']!=fingerprint(db,game):
                    reply='游戏状态已更新，请重新查询当前状态。'
            row.status=status
            db.add(Outbox(group_id=target,game_id=game_id,text=reply[:8000]))

    def next_delivery(self):
        with self.store.session.begin() as db:
            candidate=db.scalar(select(Outbox).where(Outbox.status=='queued').order_by(Outbox.created_at).limit(1))
            if not candidate:return None
            # Share the deletion/management lock order: game before message.
            if candidate.game_id and not db.scalar(select(Game).where(Game.id==candidate.game_id).with_for_update()):return None
            row = db.scalar(select(Outbox).where(Outbox.id==candidate.id,Outbox.status == "queued").with_for_update(skip_locked=True))
            if not row:
                return None
            channel = db.get(Channel, row.group_id)
            if not channel or channel.game_id != row.game_id:
                row.status = "cancelled"
                return None
            row.status = "sending"
            return row.id, row.group_id, row.text

    def delivered(self, delivery_id, status):
        with self.store.session.begin() as db:
            row = db.get(Outbox, delivery_id)
            if row:
                row.status = status

    async def pump(self):
        while True:
            try:
                await asyncio.to_thread(self.process_inbox)
                self.player_tasks={k:t for k,t in self.player_tasks.items() if not t.done()}
                if len(self.player_tasks)<4:
                    private=await asyncio.to_thread(self.claim_private)
                    if private:
                        self.player_tasks[private[0]]=asyncio.create_task(self.process_private(private))
                if self.socket and time.monotonic() - self.last_sent >= 1:
                    item = await asyncio.to_thread(self.next_delivery)
                    if item:
                        delivery_id, group, text = item
                        try:
                            private=group.startswith('private-')
                            await self.call('send_private_msg' if private else 'send_group_msg',
                                {('user_id' if private else 'group_id'):int(group.removeprefix('private-')), 'message':[{'type':'text','data':{'text':text}}]})
                            status = "sent"
                        except ValueError:
                            status = "failed"
                        except (ConnectionError, TimeoutError, RuntimeError):
                            status = "unknown"
                        await asyncio.to_thread(self.delivered, delivery_id, status)
                        self.last_sent = time.monotonic()
            except asyncio.CancelledError:
                for task in self.player_tasks.values():
                    task.cancel()
                await asyncio.gather(*self.player_tasks.values(),return_exceptions=True)
                raise
            except Exception as exc:
                log.error("Queue processing error: %s", type(exc).__name__)
            await asyncio.sleep(.25)

