"""Narrow provider compatibility and actionable errors, never guess missing JSON."""
from copy import deepcopy
import json


def unique_pairs(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise ValueError('DUPLICATE_JSON_KEY')
        value[key]=item
    return value


def arguments(text,strict=True):
    return json.loads(text,strict=strict,object_pairs_hook=unique_pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('NON_FINITE_JSON')))


def normalize(message):
    if not isinstance(message,dict) or message.get('_response_meta',{}).get('finish_reason')=='length':return message,[]
    if message.get('tool_calls') is not None and not isinstance(message['tool_calls'],list):return message,[]
    result=deepcopy(message);fixes=[]
    for call in result.get('tool_calls') or []:
        if not isinstance(call,dict) or not isinstance(call.get('function'),dict):continue
        function=call['function'];value=function.get('arguments');kind=None
        if isinstance(value,dict):kind='OBJECT_ARGUMENTS'
        elif isinstance(value,str):
            try:arguments(value);continue
            except json.JSONDecodeError as error:
                if not error.msg.startswith('Invalid control character'):continue
                try:value=arguments(value,strict=False)
                except (ValueError,TypeError):continue
                kind='LITERAL_CONTROL_CHARACTER'
            except (ValueError,TypeError):continue
        if kind and isinstance(value,dict):
            try:function['arguments']=json.dumps(value,ensure_ascii=False,allow_nan=False)
            except (ValueError,TypeError):continue
            fixes.append({'tool_name':function.get('name'),'kind':kind})
    return result,fixes


def diagnostics(message):
    details=[]
    if not isinstance(message,dict) or not isinstance(message.get('tool_calls'),list):return details
    for call in message['tool_calls']:
        if not isinstance(call,dict) or not isinstance(call.get('function'),dict):continue
        function=call['function'];text=function.get('arguments');name=function.get('name')
        item={'tool_name':name if isinstance(name,str) and len(name)<=100 else '<invalid>','argument_chars':len(text) if isinstance(text,str) else None}
        try:
            value=arguments(text)
            if isinstance(value,dict):continue
            item['problem']='ARGUMENTS_NOT_OBJECT'
        except json.JSONDecodeError as error:item.update(problem=error.msg,line=error.lineno,column=error.colno,position=error.pos)
        except (ValueError,TypeError) as error:item['problem']='DUPLICATE_OR_INVALID_JSON' if isinstance(error,ValueError) else 'ARGUMENTS_NOT_STRING'
        details.append(item)
    return details


def provider_tools(tools):
    """Full executable schemas remain server-side; large catalogs are discovered on demand."""
    result=deepcopy(tools)
    for tool in result:
        function=tool.get('function',{})
        if function.get('name')!='execute_commands':continue
        schema=function['parameters'];items=schema['properties']['commands']['items']
        # Keep the real command enum and envelope, omit only per-command payload variants.
        items.pop('oneOf',None)
        schema.pop('$defs',None)
        function['description']+=' 必须先用inspect_capabilities(kinds:[命令名])按需读取具体payload schema；服务端仍逐字段完整校验。'
    return result


def repair_context(message):
    """A small private feedback excerpt helps repair syntax without replaying a huge draft."""
    excerpts=[]
    if not isinstance(message,dict) or not isinstance(message.get('tool_calls'),list):return excerpts
    for call in message['tool_calls'][:2]:
        if not isinstance(call,dict) or not isinstance(call.get('function'),dict):continue
        function=call['function'];text=function.get('arguments')
        if not isinstance(text,str):continue
        try:arguments(text);continue
        except json.JSONDecodeError as error:
            start=max(0,error.pos-120);end=min(len(text),error.pos+280)
            excerpts.append({'tool_name':function.get('name'),'excerpt_offset':start,'excerpt':text[start:end],
                             'tail':text[-200:] if len(text)>end else '', 'partial':True})
        except (ValueError,TypeError):pass
    return excerpts
