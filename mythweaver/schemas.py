from pydantic import BaseModel, ConfigDict, Field


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Input):
    password: str = Field(max_length=500)


class NewGame(Input):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)


class UpdateGame(NewGame):
    version: int = Field(ge=1)


class Definition(Input):
    version: int = Field(ge=1)
    definition: dict


class EntityInput(Input):
    type_key: str = Field(min_length=1, max_length=80)
    data: dict
    visibility: str = "private"


class UpdateEntity(EntityInput):
    version: int = Field(ge=1)
    archived: bool = False


class Submit(Input):
    interaction: str = Field(default="_free", max_length=80)
    data: dict


class DecisionResume(Input):
    id:str=Field(min_length=1,max_length=36)
    version:int=Field(ge=1)
    data:dict


class Decision(Input):
    decision: str
    result: str = Field(min_length=1, max_length=8000)
    resume_flow:DecisionResume|None=None


class Binding(Input):
    group_id: str = Field(pattern=r"^(?:private-)?[0-9]{1,25}$")


class StartFlow(Input):
    workflow: str = Field(min_length=1,max_length=80)
    data: dict = Field(default_factory=dict)


class ResumeFlow(Input):
    version: int = Field(ge=1)
    data: dict = Field(default_factory=dict)
    reason: str = Field(min_length=1,max_length=2000)
    fingerprint: str | None = Field(default=None,pattern=r'^[a-f0-9]{64}$')


class CancelFlow(Input):
    version: int = Field(ge=1)
    reason: str = Field(min_length=1,max_length=2000)


class PolicyInput(Input):
    gm_delivery:str|None=Field(default=None,pattern='^(none|private)$')
    version: int = Field(ge=0)
    paused: bool = False
    command_prefix: str | None = Field(default=None,min_length=1,max_length=30,pattern=r'^\S+$')
    prefix_requires_space: bool | None = None
    private_trigger: str | None = Field(default=None,pattern='^(prefix|all|disabled)$')
    group_trigger: str | None = Field(default=None,pattern='^(prefix|mention|all|disabled)$')
    group_input: str | None = Field(default=None,pattern='^(commands|agent)$')
    free_mode: str = 'manual'
    allow_users: list[str] = Field(default_factory=list,max_length=200)
    instructions: str = ''
    player_agent_enabled: bool = False
    player_instructions: str = ''


class MemberInput(Input):
    actor: str = Field(pattern=r'^qq:[0-9]{1,25}$')
    label: str = Field(default='',max_length=120)
    blocked: bool = False
    version: int | None = Field(default=None,ge=0)


class CheckpointCreate(Input):
    label: str = Field(min_length=1,max_length=120)


class CheckpointRestore(Input):
    id: str = Field(min_length=1,max_length=36)
    version: int = Field(ge=1)


class ScopedEntityUpdate(UpdateEntity):
    id: str = Field(min_length=1,max_length=36)


class EntityVersion(Input):
    id: str = Field(min_length=1,max_length=36)
    version: int = Field(ge=1)
    reason: str = Field(min_length=1,max_length=2000)


class RestoreEntity(EntityVersion):
    type_key: str | None = Field(default=None,min_length=1,max_length=80)
    data: dict | None = None


class Migration(Input):
    version: int = Field(ge=1)
    definition: dict
    changes: list[ScopedEntityUpdate] = Field(default_factory=list,max_length=100)
    retire: list[EntityVersion] = Field(default_factory=list,max_length=100)
    reason: str = Field(min_length=1,max_length=2000)


class RedirectFlow(Input):
    id: str = Field(min_length=1,max_length=36)
    version: int = Field(ge=1)
    step: str = Field(min_length=1,max_length=80)
    input: dict | None = None
    answers: dict = Field(default_factory=dict)
    reason: str = Field(min_length=1,max_length=2000)


class ReviseSubmission(Input):
    id: str = Field(min_length=1,max_length=36)
    version: int = Field(ge=1)
    interaction: str = Field(min_length=1,max_length=80)
    data: dict
    status: str
    result: str | None = Field(default=None,max_length=8000)
    reason: str = Field(min_length=1,max_length=2000)


class CloneGame(NewGame):
    pass


class SaveBlueprint(Input):
    name: str = Field(min_length=1,max_length=120)
    description: str = Field(default='',max_length=4000)


class CreateBlueprint(SaveBlueprint):
    definition: dict


class InstantiateBlueprint(Input):
    id: str = Field(min_length=1,max_length=36)
    label: str = Field(min_length=1,max_length=120)


class ModuleKey(Input):
    key: str = Field(pattern=r'^[a-z][a-z0-9_]{0,79}$')
    version: int = Field(ge=1)


class PutModule(ModuleKey):
    manifest: dict


class ModuleState(ModuleKey):
    enabled: bool



