"""One supported game command catalog for HTTP, batches and the administrator Agent."""
from pydantic import Field,ValidationError

from . import schemas as s
from .definitions import DomainError


class EntityUpdate(s.UpdateEntity):
    id: str = Field(min_length=1,max_length=36)


class Decide(s.Decision):
    id: str = Field(min_length=1,max_length=36)


class Resume(s.ResumeFlow):
    id: str = Field(min_length=1,max_length=36)


class Cancel(s.CancelFlow):
    id: str = Field(min_length=1,max_length=36)


COMMANDS={
    'module.put':(s.PutModule,'创建或更新运行时模块：自定义数据、交互、流程与计算函数；自动保存检查点'),
    'module.state':(s.ModuleState,'启用或停用模块，保留记录，检查依赖'),
    'module.remove':(s.ModuleKey,'移除模块定义；有现存数据或引用时拒绝，须先迁移或退役'),
    'game.create':(s.NewGame,'创建新的空白游戏，不修改当前游戏'),
    'game.update':(s.UpdateGame,'修改当前游戏名称与说明'),
    'game.clone':(s.CloneGame,'从当前定义创建独立游戏，不复制记录、权限和群绑定'),
    'definition':(s.Definition,'兼容更新完整游戏定义'),
    'definition.migrate':(s.Migration,'原子修改定义与现有记录，可退役旧记录；自动保存安全检查点'),
    'entity.create':(s.EntityInput,'创建当前定义中的记录'),
    'entity.update':(EntityUpdate,'替换记录数据、可见标记和归档状态'),
    'entity.retire':(s.EntityVersion,'从有效数据中退役记录，保留可恢复副本及审计'),
    'entity.restore':(s.RestoreEntity,'按当前定义恢复退役记录，可同时迁移类型和数据'),
    'submit':(s.Submit,'提交交互'),
    'decide':(Decide,'批准或拒绝待决定事项'),
    'submission.revise':(s.ReviseSubmission,'修订参与内容/状态/结果，不重演历史副作用'),
    'policy.update':(s.PolicyInput,'修改暂停、白名单、指令前缀、自由提交策略及管理/玩家Agent说明和开关'),
    'participant.update':(s.MemberInput,'添加参与者、改别名、封禁或恢复；不授予平台管理员身份'),
    'channel.bind':(s.Binding,'绑定QQ群号或private-QQ号私聊，不发送消息、不抢占其他游戏绑定'),
    'channel.unbind':(s.Binding,'解绑当前游戏的群，取消该群旧排队消息'),
    'checkpoint.create':(s.CheckpointCreate,'保存游戏业务状态检查点'),
    'checkpoint.restore':(s.CheckpointRestore,'恢复指定检查点，保留恢复前安全快照'),
    'blueprint.save':(s.SaveBlueprint,'保存当前定义为可复用模板，不复制实例数据'),
    'blueprint.create':(s.CreateBlueprint,'保存指定的独立模板定义，不修改当前游戏'),
    'blueprint.instantiate':(s.InstantiateBlueprint,'从指定模板创建独立游戏，不复制参与者和群绑定'),
    'flow.start':(s.StartFlow,'发起可选流程'),
    'flow.resume':(Resume,'填写人工节点并继续'),
    'flow.cancel':(Cancel,'停止后续步骤，保留已提交结果'),
    'flow.redirect':(s.RedirectFlow,'管理员接管：修订上下文、跳到指定步骤并继续，不撤销已提交步骤'),
}
from .quality import DocumentPut
COMMANDS['requirements.put']=(DocumentPut,'保存完整原文、条款到执行规则及验收场景的版本化映射；历史版本保留')


def validate_command(kind,payload):
    if not isinstance(kind,str) or kind not in COMMANDS:
        raise DomainError('FORBIDDEN_TOOL','未实现或未授权的游戏工具',403)
    try:
        return COMMANDS[kind][0].model_validate(payload).model_dump(exclude_unset=kind=='policy.update')
    except ValidationError as exc:
        issues=['.'.join(map(str,e['loc']))+': '+e['msg'] for e in exc.errors(include_input=False,include_context=False,include_url=False)[:5]]
        raise DomainError('INVALID_PLAN','命令参数校验失败；'+'；'.join(issues)) from None


def catalog():
    return [{'kind':kind,'description':description,'schema':model.model_json_schema()} for kind,(model,description) in COMMANDS.items()]


def request_key(kind,value=None):
    from uuid import uuid4
    from hashlib import sha256
    return kind+':'+(sha256(value.encode()).hexdigest() if value else uuid4().hex)
