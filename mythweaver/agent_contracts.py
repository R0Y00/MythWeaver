"""Executable contracts shared by tool presentation and call dispatch."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

from jsonschema import Draft202012Validator

from .definitions import DomainError

_mode=ContextVar('agent_execution_mode',default='execute')


@contextmanager
def execution_mode(mode):
    token=_mode.set(mode)
    try:yield
    finally:_mode.reset(token)


def require_code_execution():
    if _mode.get() in ('plan','chat'):
        raise DomainError('EXECUTION_MODE_REQUIRED','当前是方案预演，禁止运行插件代码；请改用执行任务进行实际结算或试玩',409)


def bind_command_schema(parameters):
    """Bind command names AND payload shapes to the same live command catalog."""
    from .command_catalog import catalog
    schema=deepcopy(parameters)
    if 'commands' not in schema.get('properties',{}):return schema
    variants=[]
    for index,command in enumerate(catalog()):
        payload=deepcopy(command['schema']);definitions=payload.pop('$defs',{})
        def remap(value):
            if isinstance(value,dict):
                return {k:(f'#/$defs/command_{index}_'+v[len('#/$defs/'):] if k=='$ref' and isinstance(v,str) and v.startswith('#/$defs/') else remap(v)) for k,v in value.items()}
            if isinstance(value,list):return [remap(v) for v in value]
            return value
        if definitions:
            schema.setdefault('$defs',{}).update({f'command_{index}_{k}':remap(v) for k,v in definitions.items()})
        variants.append({'type':'object','properties':{'kind':{'const':command['kind']},'payload':remap(payload)},
                         'required':['kind','payload'],'additionalProperties':False})
    schema['properties']['commands']['items']={'type':'object','properties':{'kind':{'type':'string','enum':[c['kind'] for c in catalog()]},'payload':{'type':'object'}},
                                              'required':['kind','payload'],'additionalProperties':False,'oneOf':variants}
    return schema


def validate_advertised_call(specifications,name,arguments):
    """No invented/hidden tool or malformed command reaches an executor."""
    specification=next((s['function'] for s in specifications if s['function']['name']==name),None)
    if specification is None:
        raise DomainError('FORBIDDEN_TOOL','此工具未在本轮开放，请使用当前工具目录中的名称',403)
    errors=sorted(Draft202012Validator(specification['parameters']).iter_errors(arguments),key=lambda e:str(list(e.path)))
    if errors:
        def hint(error):
            path='.'.join(map(str,error.path)) or '参数'
            if error.validator=='type':return path+'：需要'+str(error.validator_value)+'，实际'+type(error.instance).__name__
            if error.validator=='required':return path+'：缺少必需字段，请按已提供的schema补齐'
            if error.validator=='additionalProperties':return path+'：包含未定义字段，移除而非改写玩家或GM意图'
            return path+'：'+error.validator
        paths=[hint(e) for e in errors[:4]]
        raise DomainError('INVALID_TOOL_INPUT','调用未通过本轮工具协议校验；'+'；'.join(paths))
