"""Shared authoring/player contract and source-grounded action parameters.

Grounding checks literal evidence, not unrestricted natural-language meaning.
It never grants access or replaces gameplay settlement.
"""
from copy import deepcopy
import json,re
from typing import Any,Literal
from pydantic import Field,ValidationError
from sqlalchemy import select
from .schemas import Input
from .definitions import DomainError


class Option(Input):
    value:Any
    label:str=Field(min_length=1)
    aliases:list[str]=Field(default_factory=list)


class OptionSource(Input):
    type_key:str
    value_field:str='$id'
    label_field:str
    aliases_field:str|None=None


class Binding(Input):
    path:str=Field(pattern=r'^/(?:[^/~]|~[01])+(?:/(?:[^/~]|~[01])+)*$')
    kind:Literal['number','choice']
    label:str=Field(min_length=1)
    options:list[Option]=Field(default_factory=list)
    options_from:OptionSource|None=None
    units:dict[str,float]=Field(default_factory=dict)
    allow_history:bool=True


class Example(Input):
    context:list[str]=Field(default_factory=list)
    message:str=Field(min_length=1)
    meaning:str=Field(min_length=1)


class Contract(Input):
    instructions:str=''
    context_mode:Literal['conversation','message']='conversation'
    examples:list[Example]=Field(default_factory=list)
    fields:list[Binding]=Field(default_factory=list)


class Evidence(Input):
    path:str
    quote:str=Field(min_length=1)
    turn_id:str|None=None


def parts(path):return [s.replace('~1','/').replace('~0','~') for s in path[1:].split('/')]


def field_schema(schema,path):
    for part in parts(path):
        if part=='*':schema=schema.get('items',{})
        else:schema=schema.get('properties',{}).get(part,{})
        if not schema:raise DomainError('INVALID_PLAYER_CONTRACT','语义字段必须指向输入中声明的字段，数组使用*')
    return schema


def validate(spec,definition):
    try:contract=Contract.model_validate(spec['player_contract'])
    except ValidationError:raise DomainError('INVALID_PLAYER_CONTRACT','玩家语义契约格式无效') from None
    paths=set()
    for binding in contract.fields:
        schema=field_schema(spec.get('schema',{}),binding.path)
        if binding.path in paths:raise DomainError('INVALID_PLAYER_CONTRACT','语义字段重复')
        paths.add(binding.path)
        if binding.kind=='number':
            if schema.get('type') not in ('integer','number') or binding.options or binding.options_from:
                raise DomainError('INVALID_PLAYER_CONTRACT','数值语义须对应数值字段，不设置商品选项')
            if any(not u.strip() or not 0<m<float('inf') for u,m in binding.units.items()):
                raise DomainError('INVALID_PLAYER_CONTRACT','单位换算须为非空名称与正有限倍数')
        else:
            if bool(binding.options)==bool(binding.options_from) or binding.units:
                raise DomainError('INVALID_PLAYER_CONTRACT','选择语义需要静态选项或授权记录来源二选一')
            tokens={}
            for option in binding.options:
                from .definitions import schema_validate
                schema_validate(schema,option.value)
                for name in [option.label,*option.aliases]:
                    if not name.strip():raise DomainError('INVALID_PLAYER_CONTRACT','选项名称和别名不得为空')
                    token=name.casefold();value=json.dumps(option.value,sort_keys=True,ensure_ascii=False)
                    if token in tokens and tokens[token]!=value:raise DomainError('INVALID_PLAYER_CONTRACT','同一别名不能指向不同选项')
                    tokens[token]=value
            source=binding.options_from
            if source:
                typ=definition.get('types',{}).get(source.type_key,{})
                allowed=typ.get('player_access',{}).get('read_fields',[])
                fields=[source.label_field,*([source.aliases_field] if source.aliases_field else []),*([source.value_field] if source.value_field!='$id' else [])]
                if not fields or any(f not in allowed for f in fields):
                    raise DomainError('INVALID_PLAYER_CONTRACT','动态选项的值、名称、别名必须属于玩家可读字段')
    return contract


def options(db,game,actor,binding):
    if not binding.options_from:return binding.options
    from .db import Entity
    from .player import readable
    source=binding.options_from;result=[]
    for row in db.scalars(select(Entity).where(Entity.game_id==game.id,Entity.type_key==source.type_key,Entity.archived==False).order_by(Entity.id)):
        if not readable(game,row,actor):continue
        label=row.data.get(source.label_field)
        aliases=row.data.get(source.aliases_field,[]) if source.aliases_field else []
        if not isinstance(label,str) or not label or not isinstance(aliases,list) or any(not isinstance(a,str) for a in aliases):continue
        value=row.id if source.value_field=='$id' else row.data.get(source.value_field)
        result.append(Option(value=value,label=label,aliases=aliases))
    return result


def values(data,path):
    def walk(value,segments,actual):
        if not segments:yield actual,value;return
        key,*rest=segments
        if key=='*' and isinstance(value,list):
            for i,item in enumerate(value):yield from walk(item,rest,actual+'/'+str(i))
        elif isinstance(value,dict) and key in value:yield from walk(value[key],rest,actual+'/'+key.replace('~','~0').replace('/','~1'))
    return list(walk(data,parts(path),''))


def chinese_number(text):
    digits=dict(zip('零〇一二两三四五六七八九', [0,0,1,2,2,3,4,5,6,7,8,9]))
    if '点' in text:
        whole,fraction=text.split('点',1)
        return chinese_number(whole)+float('0.'+''.join(str(digits[d]) for d in fraction))
    if not any(c in text for c in '十百千万亿'):return int(''.join(str(digits[d]) for d in text))
    result=section=number=0
    for char in text:
        if char in digits:number=digits[char]
        else:
            unit={'十':10,'百':100,'千':1000,'万':10000,'亿':100000000}[char]
            if unit<10000:section+=(number or 1)*unit
            else:section=(section+number)*unit;result+=section;section=0
            number=0
    return result+section+number


def numbers(text,units):
    result=[]
    for match in re.finditer(r'[-+]?\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千万亿]+(?:点[零〇一二两三四五六七八九]+)?',text):
        token=match.group()
        try:value=float(token) if token[0] in '+-0123456789' else chinese_number(token)
        except (ValueError,KeyError):continue
        suffix=text[match.end():].lstrip()
        multiplier=next((units[u] for u in sorted(units,key=len,reverse=True) if suffix.startswith(u)),1)
        result.append(value*multiplier)
    return result


def matches(quote,choices):
    # Longest overlapping names win: “高级药水” must not also match “药水”.
    hits=[];text=quote.casefold()
    for choice in choices:
        for alias in [choice.label,*choice.aliases]:
            start=0
            while alias and (index:=text.find(alias.casefold(),start))>=0:
                hits.append((index,index+len(alias),choice));start=index+len(alias)
    return [choice for a,b,choice in hits if not any(c<=a and d>=b and (c<a or d>b) for c,d,_ in hits)]


def verify(db,game,actor,payload,evidence,prompt,history):
    raw=game.definition.get('interactions',{}).get(payload['interaction'],{}).get('player_contract')
    if not raw:return
    contract=Contract.model_validate(raw)
    try:proof=[Evidence.model_validate(item) for item in evidence]
    except (ValidationError,TypeError):raise DomainError('INTENT_MISMATCH','请按玩家原话提供目标和数量依据，尚未执行') from None
    bypath={item.path:item for item in proof}
    if len(bypath)!=len(proof):raise DomainError('INTENT_MISMATCH','同一参数的原话依据重复，尚未执行')
    history={r.id:r.prompt for r in history};groups={}
    for item in proof:
        source=history.get(item.turn_id,'') if item.turn_id else prompt
        if item.quote not in source:raise DomainError('INTENT_MISMATCH','行动依据必须属于本聊天真实原话，不能伪造历史使用标记')
    for binding in contract.fields:
        choices=options(db,game,actor,binding) if binding.kind=='choice' else []
        for path,value in values(payload['data'],binding.path):
            item=bypath.get(path)
            if not item:raise DomainError('INTENT_MISMATCH','缺少“'+binding.label+'”的原话依据；请读取选项或询问缺失内容，不能猜测')
            source=history.get(item.turn_id,'') if item.turn_id else prompt
            if item.turn_id and (contract.context_mode=='message' or not binding.allow_history or (numbers(prompt,binding.units) if binding.kind=='number' else matches(prompt,choices))):
                raise DomainError('INTENT_MISMATCH','本条消息已补充“'+binding.label+'”，不能沿用旧选择')
            if item.quote not in source:raise DomainError('INTENT_MISMATCH','“'+binding.label+'”依据不属于本聊天的真实玩家原话')
            if binding.kind=='number':
                parsed=numbers(item.quote,binding.units)
                if len(parsed)>1:raise DomainError('INTENT_MISMATCH','“'+binding.label+'”的依据包含多个数值。只引用对应数量与单位的短片段，不带“一笔/上一局”等其他数字；原话有明确目标时无需反复询问。')
                valid=parsed==[value]
            else:
                found=matches(item.quote,choices)
                valid=bool(found) and all(c.value==value for c in found)
            if not valid:raise DomainError('INTENT_MISMATCH','“'+binding.label+'”与原话中的数量或选项不一致；请重新解析，不能替换目标')
            # Sibling choice/count fields must refer to the same local phrase,
            # preventing the quantity of one list item being borrowed for another.
            parent=path.rsplit('/',1)[0];group=groups.setdefault(parent,[])
            group.append((binding.kind,item.quote,item.turn_id))
    for fields in groups.values():
        current=[(kind,quote) for kind,quote,turn in fields if turn is None]
        if {'choice','number'}<=set(kind for kind,_ in current) and len({quote for _,quote in current})!=1:
            raise DomainError('INTENT_MISMATCH','同一项的目标和数量必须来自同一段玩家原话，不能串项')


def public_contract(spec):
    return Contract.model_validate(spec['player_contract']).model_dump() if 'player_contract' in spec else None


def action_tool_name(key):
    import hashlib
    return 'act_'+key[:42]+'_'+hashlib.sha256(key.encode()).hexdigest()[:8]


def tools(interactions):
    evidence={'type':'array','items':Evidence.model_json_schema()}
    result=[]
    for key,spec in interactions.items():
        contract=spec.get('player_contract',{})
        result.append({'type':'function','function':{'name':action_tool_name(key),
            'description':spec.get('label',key)+'。'+spec.get('description','')+'。'+contract.get('instructions','')+'。data使用本工具的完整输入结构；语义字段的evidence引用玩家原话，历史依据填写turn_id。',
            'parameters':{'type':'object','properties':{'data':spec['schema'],'evidence':evidence},'required':['data'],'additionalProperties':False}}})
    return result


def normalize(message,interactions):
    names={action_tool_name(key):key for key in interactions}
    result=deepcopy(message);calls=result.get('tool_calls',[])
    for call in calls:
        function=call.get('function',{})
        if function.get('name') in names:
            try:
                args=json.loads(function['arguments'])
                if not isinstance(args,dict) or set(args)-{'data','evidence'} or 'data' not in args:raise ValueError()
                function['arguments']=json.dumps({'interaction':names[function['name']],**args},ensure_ascii=False)
                function['name']='submit_action'
            except (KeyError,TypeError,ValueError):raise DomainError('INVALID_ACTION','行动参数格式无效') from None
    if len(calls)>1 and all(c.get('function',{}).get('name')=='submit_action' for c in calls):
        try:actions=[json.loads(c['function']['arguments']) for c in calls]
        except (ValueError,TypeError,KeyError):raise DomainError('INVALID_ACTION','多行动参数无效') from None
        if all(isinstance(a,dict) and interactions.get(a.get('interaction'),{}).get('player_batch') for a in actions):
            result['tool_calls']=[{'id':'batch','type':'function','function':{'name':'submit_actions','arguments':json.dumps({'actions':actions},ensure_ascii=False)}}]
    return result
