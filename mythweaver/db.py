from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, create_engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def uid():
    return str(uuid4())


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)


class Game(Base):
    __tablename__ = "games"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(4000), default="")
    definition: Mapped[dict] = mapped_column(JSONB, default=lambda: {"types": {}, "interactions": {}})
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Entity(Base):
    __tablename__ = "entities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), index=True)
    type_key: Mapped[str] = mapped_column(String(80))
    data: Mapped[dict] = mapped_column(JSONB)
    visibility: Mapped[str] = mapped_column(String(20), default="private")
    version: Mapped[int] = mapped_column(Integer, default=1)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class EntityRevision(Base):
    __tablename__ = "entity_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    last_version: Mapped[int] = mapped_column(Integer)


class Submission(Base):
    __tablename__ = "submissions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), index=True)
    interaction: Mapped[str] = mapped_column(String(80))
    actor: Mapped[str] = mapped_column(String(120))
    data: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20))
    definition_version: Mapped[int] = mapped_column(Integer)
    result: Mapped[str | None] = mapped_column(String(8000), nullable=True)
    dependencies: Mapped[dict] = mapped_column(JSONB, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Event(Base):
    __tablename__ = "events"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), index=True)
    actor: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SubmissionRevision(Base):
    __tablename__ = 'submission_revisions'
    id: Mapped[str] = mapped_column(String(36),primary_key=True)
    last_version: Mapped[int] = mapped_column(Integer)


class Command(Base):
    __tablename__ = "commands"
    __table_args__ = (UniqueConstraint("game_id", "actor", "key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"))
    actor: Mapped[str] = mapped_column(String(120))
    key: Mapped[str] = mapped_column(String(120))
    digest: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSONB)


class Channel(Base):
    __tablename__ = "channels"
    group_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"))


class AgentPlan(Base):
    __tablename__ = "agent_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), index=True)
    prompt: Mapped[str] = mapped_column(String(8000))
    explanation: Mapped[str] = mapped_column(String(8000))
    commands: Mapped[list] = mapped_column(JSONB)
    preview: Mapped[list] = mapped_column(JSONB)
    impact: Mapped[dict] = mapped_column(JSONB,default=dict)
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="prepared")
    result: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FlowRun(Base):
    __tablename__ = "flow_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), index=True)
    workflow: Mapped[str] = mapped_column(String(80))
    actor: Mapped[str] = mapped_column(String(120))
    submission_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    context: Mapped[dict] = mapped_column(JSONB, default=dict)
    current_step: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30), default="running")
    version: Mapped[int] = mapped_column(Integer, default=1)
    definition_version: Mapped[int] = mapped_column(Integer)
    dependencies: Mapped[dict] = mapped_column(JSONB, default=dict)
    locks: Mapped[list] = mapped_column(JSONB, default=list)
    history: Mapped[list] = mapped_column(JSONB, default=list)
    result: Mapped[str] = mapped_column(String(4000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlatformConfig(Base):
    __tablename__ = "platform_config"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer, default=1)


class ModelProvider(Base):
    __tablename__ = "model_providers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120))
    base_url: Mapped[str] = mapped_column(String(1000))
    encrypted_key: Mapped[str] = mapped_column(String(4000), default="")
    models: Mapped[list] = mapped_column(JSONB, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    token_parameter: Mapped[str] = mapped_column(String(40), default="max_tokens")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentSession(Base):
    __tablename__='agent_sessions'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    game_id: Mapped[str]=mapped_column(ForeignKey('games.id'),index=True)
    title: Mapped[str]=mapped_column(String(120),default='新会话')
    mode: Mapped[str]=mapped_column(String(20),default='harness')
    approval_mode: Mapped[str]=mapped_column(String(20),default='manual',server_default='manual')
    pinned: Mapped[bool]=mapped_column(Boolean,default=False)
    archived: Mapped[bool]=mapped_column(Boolean,default=False)
    deleted: Mapped[bool]=mapped_column(Boolean,default=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), index=True)
    session_id: Mapped[str | None]=mapped_column(ForeignKey('agent_sessions.id'),nullable=True,index=True)
    followups: Mapped[list]=mapped_column(JSONB,default=list)
    kind: Mapped[str] = mapped_column(String(20), default="plan")
    prompt: Mapped[str] = mapped_column(String(8000))
    answer: Mapped[str] = mapped_column(String(16000), default="")
    status: Mapped[str] = mapped_column(String(30), default="running")
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("agent_plans.id"), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool_trace: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GamePolicy(Base):
    __tablename__ = "game_policies"
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), primary_key=True)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Participant(Base):
    __tablename__ = "participants"
    __table_args__ = (UniqueConstraint("game_id", "actor"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), index=True)
    actor: Mapped[str] = mapped_column(String(120))
    label: Mapped[str] = mapped_column(String(120), default="")
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Checkpoint(Base):
    __tablename__ = "checkpoints"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), index=True)
    label: Mapped[str] = mapped_column(String(120))
    snapshot: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RetiredEntity(Base):
    __tablename__ = 'retired_entities'
    id: Mapped[str] = mapped_column(String(36),primary_key=True)
    game_id: Mapped[str] = mapped_column(ForeignKey('games.id'),index=True)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow)


class Blueprint(Base):
    __tablename__ = "blueprints"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(4000), default="")
    definition: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SystemLog(Base):
    __tablename__ = "system_logs"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(80))
    detail: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Inbox(Base):
    __tablename__ = "inbox"
    route: Mapped[str]=mapped_column(String(20),default='commands')
    key: Mapped[str] = mapped_column(String(250), primary_key=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"))
    actor: Mapped[str] = mapped_column(String(120))
    content: Mapped[str] = mapped_column(String(16000))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlayerTurn(Base):
    __tablename__ = 'player_turns'
    __table_args__ = (UniqueConstraint('game_id','actor','key'),)
    id: Mapped[str] = mapped_column(String(36),primary_key=True,default=uid)
    game_id: Mapped[str] = mapped_column(ForeignKey('games.id'),index=True)
    actor: Mapped[str] = mapped_column(String(120))
    key: Mapped[str] = mapped_column(String(120))
    prompt: Mapped[str] = mapped_column(String(8000))
    answer: Mapped[str] = mapped_column(String(16000),default='')
    status: Mapped[str] = mapped_column(String(20),default='running')
    result: Mapped[dict] = mapped_column(JSONB,default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow)


class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    group_id: Mapped[str] = mapped_column(String(80))
    game_id: Mapped[str | None] = mapped_column(ForeignKey("games.id"), nullable=True)
    text: Mapped[str] = mapped_column(String(8000))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PluginRelease(Base):
    __tablename__='plugin_releases'
    __table_args__=(UniqueConstraint('game_id','key','revision'),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    game_id: Mapped[str]=mapped_column(ForeignKey('games.id'),index=True)
    key: Mapped[str]=mapped_column(String(24))
    revision: Mapped[int]=mapped_column(Integer)
    manifest: Mapped[dict]=mapped_column(JSONB)
    files: Mapped[dict]=mapped_column(JSONB)
    digest: Mapped[str]=mapped_column(String(64))
    status: Mapped[str]=mapped_column(String(24),default='draft')
    enabled: Mapped[bool]=mapped_column(Boolean,default=False)
    report: Mapped[dict]=mapped_column(JSONB,default=dict)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class HarnessTask(Base):
    __tablename__='harness_tasks'
    id: Mapped[str]=mapped_column(ForeignKey('agent_runs.id'),primary_key=True)
    game_id: Mapped[str]=mapped_column(ForeignKey('games.id'),index=True)
    messages: Mapped[list]=mapped_column(JSONB,default=list)
    step: Mapped[int]=mapped_column(Integer,default=0)
    max_steps: Mapped[int]=mapped_column(Integer,default=24)
    pending_calls: Mapped[list]=mapped_column(JSONB,default=list)
    pending_tools: Mapped[list]=mapped_column(JSONB,default=list)


class ToolExecution(Base):
    __tablename__='tool_executions'
    __table_args__=(UniqueConstraint('run_id','call_id'),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    run_id: Mapped[str]=mapped_column(ForeignKey('agent_runs.id'),index=True)
    call_id: Mapped[str]=mapped_column(String(200))
    name: Mapped[str]=mapped_column(String(100))
    arguments: Mapped[dict]=mapped_column(JSONB)
    status: Mapped[str]=mapped_column(String(24),default='running')
    result: Mapped[dict]=mapped_column(JSONB,default=dict)
    approval: Mapped[dict]=mapped_column(JSONB,default=dict)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class GameDocument(Base):
    __tablename__='game_documents'
    __table_args__=(UniqueConstraint('game_id','key','revision'),)
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    game_id:Mapped[str]=mapped_column(ForeignKey('games.id'),index=True)
    key:Mapped[str]=mapped_column(String(80))
    revision:Mapped[int]=mapped_column(Integer)
    content:Mapped[dict]=mapped_column(JSONB)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class PlayerChoice(Base):
    __tablename__='player_choices'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    game_id:Mapped[str]=mapped_column(ForeignKey('games.id'),index=True)
    actor:Mapped[str]=mapped_column(String(120))
    audience:Mapped[str]=mapped_column(String(20))
    conversation:Mapped[str]=mapped_column(String(80))
    payload:Mapped[dict]=mapped_column(JSONB)
    dependencies:Mapped[dict]=mapped_column(JSONB)
    quote:Mapped[str]=mapped_column(String(8000))
    status:Mapped[str]=mapped_column(String(24),default='waiting')
    expires_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class GameTrial(Base):
    __tablename__='game_trials'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    game_id:Mapped[str]=mapped_column(ForeignKey('games.id'),index=True)
    label:Mapped[str]=mapped_column(String(120))
    version:Mapped[int]=mapped_column(Integer,default=1)
    source_stamp:Mapped[str]=mapped_column(String(64))
    allow_external:Mapped[bool]=mapped_column(Boolean,default=False)
    baseline:Mapped[dict]=mapped_column(JSONB)
    state:Mapped[dict]=mapped_column(JSONB)
    turns:Mapped[list]=mapped_column(JSONB,default=list)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class AcceptanceRun(Base):
    __tablename__='acceptance_runs'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    game_id:Mapped[str]=mapped_column(ForeignKey('games.id'),index=True)
    document_id:Mapped[str]=mapped_column(String(36))
    config_stamp:Mapped[str]=mapped_column(String(64))
    results:Mapped[dict]=mapped_column(JSONB,default=dict)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class Store:
    def __init__(self, url):
        self.engine = create_engine(url, pool_pre_ping=True)
        self.session = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self):
        Base.metadata.create_all(self.engine)
        with self.session.begin() as db:
            # Explicit additive migration for databases created by the initial build.
            if not db.get(SchemaMigration, 1):
                db.execute(text("ALTER TABLE outbox ADD COLUMN IF NOT EXISTS game_id VARCHAR(36) REFERENCES games(id)"))
                db.execute(text("UPDATE outbox SET game_id = channels.game_id FROM channels WHERE outbox.group_id = channels.group_id AND outbox.game_id IS NULL"))
                db.execute(text("UPDATE outbox SET status = 'cancelled' WHERE game_id IS NULL AND status = 'queued'"))
                db.add(SchemaMigration(version=1))
            if not db.get(SchemaMigration,2):
                db.execute(text("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS dependencies JSONB NOT NULL DEFAULT '{}'::jsonb"))
                db.add(SchemaMigration(version=2))
            if not db.get(SchemaMigration,3):
                # flow_runs is a new table, created above; existing rows are unchanged.
                db.add(SchemaMigration(version=3))
            if not db.get(SchemaMigration,4):
                db.execute(text("ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS tool_trace JSONB NOT NULL DEFAULT '[]'::jsonb"))
                db.add(SchemaMigration(version=4))
            if not db.get(SchemaMigration,5):
                db.execute(text('ALTER TABLE submissions ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1'))
                db.execute(text('ALTER TABLE participants ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1'))
                db.add(SchemaMigration(version=5))
            if not db.get(SchemaMigration,6):
                db.execute(text("ALTER TABLE agent_plans ADD COLUMN IF NOT EXISTS impact JSONB NOT NULL DEFAULT '{}'::jsonb"))
                db.add(SchemaMigration(version=6))
            if not db.get(SchemaMigration,7):
                db.execute(text('INSERT INTO submission_revisions(id,last_version) SELECT id,version FROM submissions ON CONFLICT (id) DO UPDATE SET last_version=GREATEST(submission_revisions.last_version,EXCLUDED.last_version)'))
                db.add(SchemaMigration(version=7))
            if not db.get(SchemaMigration,8):
                db.add(SchemaMigration(version=8))
            if not db.get(SchemaMigration,9):
                db.add(SchemaMigration(version=9))
            if not db.get(SchemaMigration,10):
                db.execute(text('ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS session_id VARCHAR(36) REFERENCES agent_sessions(id)'))
                db.execute(text("ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS followups JSONB NOT NULL DEFAULT '[]'::jsonb"))
                db.execute(text('CREATE INDEX IF NOT EXISTS ix_agent_runs_session_id ON agent_runs(session_id)'))
                db.execute(text("INSERT INTO agent_sessions(id,game_id,title,mode,pinned,archived,deleted,created_at,updated_at) SELECT id,game_id,left(prompt,80),kind,false,false,false,created_at,created_at FROM agent_runs WHERE session_id IS NULL ON CONFLICT(id) DO NOTHING"))
                db.execute(text('UPDATE agent_runs SET session_id=id WHERE session_id IS NULL'))
                db.add(SchemaMigration(version=10))
            if not db.get(SchemaMigration,11):
                db.execute(text("ALTER TABLE agent_sessions ADD COLUMN IF NOT EXISTS approval_mode VARCHAR(20) NOT NULL DEFAULT 'manual'"))
                db.execute(text("ALTER TABLE harness_tasks ADD COLUMN IF NOT EXISTS pending_calls JSONB NOT NULL DEFAULT '[]'::jsonb"))
                db.execute(text("ALTER TABLE harness_tasks ADD COLUMN IF NOT EXISTS pending_tools JSONB NOT NULL DEFAULT '[]'::jsonb"))
                db.execute(text("ALTER TABLE tool_executions ADD COLUMN IF NOT EXISTS approval JSONB NOT NULL DEFAULT '{}'::jsonb"))
                db.execute(text("ALTER TABLE inbox ADD COLUMN IF NOT EXISTS route VARCHAR(20) NOT NULL DEFAULT 'commands'"))
                db.execute(text("UPDATE inbox SET route='agent' WHERE key LIKE :pattern"),{'pattern':'%:private-%'})
                db.add(SchemaMigration(version=11))
            if not db.get(SchemaMigration,12):
                # Additive tables are created above: documents, player choices,
                # persistent isolated trials and acceptance evidence.
                db.add(SchemaMigration(version=12))
        # A crashed sender cannot know whether QQ accepted an in-flight message.
        with self.session.begin() as db:
            from sqlalchemy import update
            db.execute(update(Outbox).where(Outbox.status == "sending").values(status="unknown"))
            db.execute(update(AgentRun).where(AgentRun.status == "running").values(status="interrupted", error_code="PROCESS_RESTARTED"))
            db.execute(update(PlayerTurn).where(PlayerTurn.status=='running').values(status='interrupted'))
            db.execute(update(Inbox).where(Inbox.status=='processing').values(status='interrupted'))
            db.execute(update(PluginRelease).where(PluginRelease.status.in_(['installing','testing'])).values(status='interrupted',enabled=False))
            db.execute(update(ToolExecution).where(ToolExecution.status=='running').values(status='unknown'))

