import re

from jsonschema import Draft202012Validator, ValidationError


class DomainError(Exception):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def check_schema(schema, depth=0):
    """Only local, bounded schemas; never resolve network or filesystem references."""
    if depth > 16:
        raise DomainError("INVALID_SCHEMA", "字段嵌套最多 16 层")
    if isinstance(schema, dict):
        if any(k in schema for k in ("$ref", "$dynamicRef", "$id", "pattern", "patternProperties")):
            raise DomainError("INVALID_SCHEMA", "首版不支持引用或正则 schema")
        for value in schema.values():
            check_schema(value, depth + 1)
    elif isinstance(schema, list):
        for value in schema:
            check_schema(value, depth + 1)


def schema_validate(schema, data):
    try:
        Draft202012Validator(schema).validate(data)
    except ValidationError as exc:
        path = ".".join(str(p) for p in exc.path)
        # Do not echo values or private records into errors.
        raise DomainError("VALIDATION_FAILED", f"字段 {path or '(根对象)'} 不符合定义：{exc.validator}") from None


def validate_definition(value):
    if not isinstance(value,dict) or set(value) - {"types", "interactions", "workflows", "functions", "modules"}:
        raise DomainError("INVALID_DEFINITION", "定义支持 types、interactions、workflows、functions 和 modules")
    if 'modules' in value:
        from .modules import materialize
        value=materialize(value)
    from .functions import validate_functions
    validate_functions(value)
    for section in ("types", "interactions"):
        items = value.get(section, {})
        if not isinstance(items, dict) or len(items) > 50:
            raise DomainError("INVALID_DEFINITION", "定义必须是对象，每类最多 50 项")
        for key, spec in items.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", key) or not isinstance(spec, dict):
                raise DomainError("INVALID_DEFINITION", "标识符须为英文小写、数字和下划线")
            allowed = {"label", "schema", "enabled"}
            if section == 'types':
                allowed.add('player_access')
                if 'player_access' in spec:
                    from .player import validate_access
                    validate_access(spec)
            if section == "interactions":
                allowed |= {"mode", "response", "allow_users", "effects", "condition", "workflow", "player_enabled", "player_description", "group_enabled", "extension", "confirmation", "gm_delivery"}
                if 'confirmation' in spec:
                    from .choices import validate
                    validate(spec['confirmation'])
                if 'gm_delivery' in spec and spec['gm_delivery'] not in ('none','private'):
                    raise DomainError('INVALID_DEFINITION','GM裁决投递方式只能是none或private')
                if not isinstance(spec.get('group_enabled',False),bool):raise DomainError('INVALID_DEFINITION','群聊交互开关必须为布尔值')
                if 'extension' in spec:
                    from .extensions import validate
                    validate(value,spec)
                    if spec.get('mode','manual') not in ('manual','rule'):
                        raise DomainError('INVALID_EXTENSION','交互扩展用于rule或manual模式；流程扩展应配置在步骤中')
                if not isinstance(spec.get('player_enabled',False),bool) or not isinstance(spec.get('player_description',''),str) or len(spec.get('player_description',''))>2000:
                    raise DomainError('INVALID_DEFINITION','玩家交互开关或说明无效')
                users = spec.get("allow_users",[])
                if not isinstance(users,list) or len(users)>200 or any(not isinstance(u,str) or not re.fullmatch(r"qq:[0-9]{1,25}",u) for u in users):
                    raise DomainError("INVALID_DEFINITION","交互允许用户须为 qq:数字 标识列表")
                if spec.get("mode", "manual") not in ("manual", "record", "fixed", "rule", "flow"):
                    raise DomainError("INVALID_DEFINITION", "交互处理方式无效")
                if 'condition' in spec:
                    from .conditions import validate_condition
                    validate_condition(spec['condition'],value)
                if 'workflow' in spec and (not isinstance(spec['workflow'],str) or spec.get('mode')!='flow'):
                    raise DomainError('INVALID_FLOW','仅流程交互可以设置关联流程')
                from .rules import check_effects
                check_effects(value,spec)
                if not isinstance(spec.get("response", ""), str) or len(spec.get("response", "")) > 4000:
                    raise DomainError("INVALID_DEFINITION", "固定结果过长")
            if set(spec) - allowed or not isinstance(spec.get("enabled", True), bool):
                raise DomainError("INVALID_DEFINITION", "定义包含不支持的配置")
            if not isinstance(spec.get("label", key), str) or len(spec.get("label", key)) > 120:
                raise DomainError("INVALID_DEFINITION", "名称过长或类型错误")
            schema = spec.get("schema", {"type": "object"})
            if not isinstance(schema, dict) or schema.get("type") != "object":
                raise DomainError("INVALID_SCHEMA", "类型和交互输入必须为 object schema")
            check_schema(schema)
            from .references import check_reference_schema
            check_reference_schema(schema,value)
            try:
                Draft202012Validator.check_schema(schema)
            except Exception:
                raise DomainError("INVALID_SCHEMA", "无效的 JSON Schema") from None
    from .workflows import validate_workflows
    validate_workflows(value)
    from .functions import validate_usage
    validate_usage(value)
    return {"types": value.get("types", {}), "interactions": value.get("interactions", {}),
            **{k:value[k] for k in ('workflows','functions','modules') if k in value}}

