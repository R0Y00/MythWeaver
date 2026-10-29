"""Bounded expression functions authored at runtime, independent of game concepts."""
from copy import deepcopy
import json
import math
import re

from .definitions import DomainError,schema_validate,check_schema

ARITIES={'add':(2,20),'subtract':(2,2),'multiply':(2,20),'divide':(2,2),'mod':(2,2),
         'min':(1,20),'max':(1,20),'round':(1,1),'floor':(1,1),'ceil':(1,1),
         'eq':(2,2),'ne':(2,2),'gt':(2,2),'gte':(2,2),'lt':(2,2),'lte':(2,2),
         'not':(1,1),'all':(1,20),'any':(1,20),'if':(3,3),'get':(2,3),
         'count':(1,1),'sum':(1,1),'concat':(2,20),'contains':(2,2),
         'map':(2,2),'filter':(2,2),'slice':(3,3)}


def invalid(message='函数表达式无效'):
    raise DomainError('INVALID_FUNCTION',message)


def check_expression(node,functions,depth=0,calls=None,budget=None):
    calls=calls if calls is not None else set()
    budget=budget if budget is not None else [0]
    budget[0]+=1
    if budget[0]>1000 or depth>24 or not isinstance(node,dict):
        invalid('表达式最多1000个节点、24层嵌套')
    keys=set(node)
    children=[]
    if keys=={'literal'}:
        try:
            encoded=json.dumps(node['literal'],allow_nan=False)
        except (ValueError,TypeError,OverflowError):
            invalid('常量必须是有限JSON值')
        if len(encoded)>16000:
            invalid('表达式常量过长')
    elif keys=={'path'}:
        if not isinstance(node['path'],str) or not re.fullmatch(r'(input|current|actor|item|index)(\.[a-zA-Z0-9_-]+){0,16}',node['path']):
            invalid('路径仅可读取input/current/actor/item/index及其字段')
    elif keys=={'array'} and isinstance(node['array'],list):
        children=node['array']
    elif keys=={'object'} and isinstance(node['object'],dict):
        if any(not isinstance(k,str) or len(k)>120 for k in node['object']):
            invalid()
        children=list(node['object'].values())
    elif keys=={'call','input'}:
        if not isinstance(node['call'],str) or node['call'] not in functions:
            invalid('调用的函数未定义')
        calls.add(node['call'])
        children=[node['input']]
    elif keys=={'op','args'}:
        op,args=node['op'],node['args']
        if not isinstance(op,str) or op not in ARITIES or not isinstance(args,list) or not ARITIES[op][0]<=len(args)<=ARITIES[op][1]:
            invalid('操作名称或参数数量无效')
        children=args
    else:
        invalid()
    for child in children:
        check_expression(child,functions,depth+1,calls,budget)
    return calls


def validate_functions(definition):
    functions=definition.get('functions',{})
    if not isinstance(functions,dict) or len(functions)>100:
        invalid('最多定义100个函数')
    graph={}
    for key,spec in functions.items():
        if (not isinstance(key,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}',key) or not isinstance(spec,dict)
            or set(spec)-{'label','enabled','schema','result'} or not isinstance(spec.get('enabled',True),bool)
            or not isinstance(spec.get('label',key),str) or len(spec.get('label',key))>120 or 'result' not in spec):
            invalid('函数标识或定义无效')
        schema=spec.get('schema',{'type':'object'})
        if not isinstance(schema,dict) or schema.get('type')!='object':
            invalid('函数参数必须为对象schema')
        check_schema(schema)
        from jsonschema import Draft202012Validator
        try:
            Draft202012Validator.check_schema(schema)
        except Exception:
            invalid('函数参数schema无效')
        graph[key]=check_expression(spec['result'],functions)
    visiting,visited=set(),set()
    def visit(key):
        if key in visiting:
            invalid('函数不能循环或递归调用')
        if key in visited:
            return
        visiting.add(key)
        for child in graph[key]:
            visit(child)
        visiting.remove(key)
        visited.add(key)
    for key in graph:
        visit(key)
        if functions[key].get('enabled',True) and any(not functions[child].get('enabled',True) for child in graph[key]):
            invalid('启用的函数仍引用已停用函数')


def validate_usage(definition):
    specs=[s for s in definition.get('interactions',{}).values() if s.get('enabled',True)]
    for flow in definition.get('workflows',{}).values():
        if flow.get('enabled',True):
            specs.extend(flow.get('steps',{}).values())
    functions=definition.get('functions',{})
    for spec in specs:
        for effect in spec.get('effects',[]):
            if effect.get('operation') in ('compute','plugin'):
                calls=check_expression(effect['expression'],functions)
                if any(not functions[key].get('enabled',True) for key in calls):
                    invalid('启用的交互或流程仍引用已停用函数，请先调整依赖')


class Runtime:
    def __init__(self,definition):
        self.functions=definition.get('functions',{})
        self.remaining=2000

    def bounded(self,value):
        try:
            if len(json.dumps(value,ensure_ascii=False,allow_nan=False))>32000:
                invalid('函数结果超过32000字符')
        except (ValueError,TypeError,OverflowError):
            invalid('函数结果必须是有限JSON值')
        return value

    def evaluate(self,node,scope,depth=0):
        self.remaining-=1
        if self.remaining<0 or depth>40:
            invalid('函数执行超过步骤或深度上限')
        def run(n,local=None):
            return self.evaluate(n,scope if local is None else local,depth+1)
        try:
            if 'literal' in node:
                result=deepcopy(node['literal'])
            elif 'path' in node:
                result=scope
                for part in node['path'].split('.'):
                    result=result[int(part)] if isinstance(result,list) and part.isdecimal() else result[part]
            elif 'array' in node:
                result=[run(n) for n in node['array']]
            elif 'object' in node:
                result={k:run(n) for k,n in node['object'].items()}
            elif 'call' in node:
                spec=self.functions[node['call']]
                if not spec.get('enabled',True):
                    invalid('调用的函数已停用')
                data=run(node['input'])
                schema_validate(spec.get('schema',{'type':'object'}),data)
                result=run(spec['result'],{'input':data})
            else:
                op,args=node['op'],node['args']
                if op=='if':
                    test=run(args[0])
                    if type(test) is not bool:
                        invalid('if条件必须是布尔值')
                    result=run(args[1] if test else args[2])
                elif op in ('map','filter'):
                    source=run(args[0])
                    if not isinstance(source,list) or len(source)>200:
                        invalid('map/filter需要最多200项的列表')
                    result=[]
                    for index,item in enumerate(source):
                        value=run(args[1],{**scope,'item':item,'index':index})
                        if op=='map':
                            result.append(value)
                        else:
                            if type(value) is not bool:
                                invalid('filter条件必须是布尔值')
                            if value:
                                result.append(item)
                else:
                    values=[run(n) for n in args]
                    result=self.operation(op,values)
            return self.bounded(result)
        except (KeyError,IndexError,TypeError,ValueError,ZeroDivisionError,OverflowError):
            invalid('函数输入、路径或计算结果无效')

    def operation(self,op,v):
        numeric={'add','subtract','multiply','divide','mod','min','max','round','floor','ceil','gt','gte','lt','lte'}
        if op in numeric and any(type(x) not in (int,float) or not math.isfinite(x) for x in v):
            invalid('数值操作只接受有限数字')
        if op=='add':return sum(v)
        if op=='subtract':return v[0]-v[1]
        if op=='multiply':return math.prod(v)
        if op=='divide':return v[0]/v[1]
        if op=='mod':return v[0]%v[1]
        if op=='min':return min(v)
        if op=='max':return max(v)
        if op=='round':return round(v[0])
        if op=='floor':return math.floor(v[0])
        if op=='ceil':return math.ceil(v[0])
        if op=='eq':return v[0]==v[1]
        if op=='ne':return v[0]!=v[1]
        if op=='gt':return v[0]>v[1]
        if op=='gte':return v[0]>=v[1]
        if op=='lt':return v[0]<v[1]
        if op=='lte':return v[0]<=v[1]
        if op in ('not','all','any'):
            if any(type(x) is not bool for x in v):invalid('逻辑操作需要布尔值')
            return not v[0] if op=='not' else all(v) if op=='all' else any(v)
        if op=='count':
            if not isinstance(v[0],(list,dict,str)):invalid('count需要列表、对象或文本')
            return len(v[0])
        if op=='sum':
            if not isinstance(v[0],list):invalid('sum需要数值列表')
            if not v[0]:return 0
            return self.operation('add',v[0])
        if op=='concat':
            if all(isinstance(x,str) for x in v):return ''.join(v)
            if all(isinstance(x,list) for x in v):return [item for x in v for item in x]
            invalid('concat需要同类型文本或列表')
        if op=='contains':
            if not isinstance(v[0],(list,dict,str)):invalid('contains需要列表、对象或文本')
            return v[1] in v[0]
        if op=='get':
            if not isinstance(v[0],dict) or not isinstance(v[1],str):invalid('get需要对象和字段名')
            return v[0].get(v[1],v[2] if len(v)==3 else None)
        if op=='slice':
            if not isinstance(v[0],(list,str)) or type(v[1]) is not int or type(v[2]) is not int:invalid('slice需要列表/文本及整数范围')
            return v[0][v[1]:v[2]]
        invalid()


def capabilities():
    from .extensions import capabilities as extension_capabilities
    return {'extensions':extension_capabilities(),'forms':{'literal':{'literal':'任意JSON常量'},'path':{'path':'input.field'},
                     'array':{'array':[{'literal':1}]},'object':{'object':{'field':{'literal':1}}},
                     'call':{'call':'函数标识','input':{'object':{'field':{'path':'input.field'}}}},
                     'operation':{'op':'add','args':[{'path':'input.value'},{'literal':1}]}},
            'operations':{key:{'min_args':a,'max_args':b} for key,(a,b) in ARITIES.items()},
            'scope':'效果计算可读取input/current/actor；函数体只读取input；map/filter内额外提供item/index。',
            'limits':'最多2000次求值、40层执行深度；无网络/文件/SQL/宿主代码；结果最多32000字符。'}
