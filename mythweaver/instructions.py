"""Keep stored instructions whole; page only explicit tool-read responses."""
from pydantic import Field

from .schemas import Input


class InstructionRead(Input):
    instruction_offset:int=Field(default=0,ge=0)
    instruction_limit:int=Field(default=8000,ge=1,le=10000)


def page(values,offset=0,limit=8000):
    result=dict(values);metadata={}
    for key in ('instructions','agent_instructions','player_instructions'):
        text=result.get(key)
        if isinstance(text,str) and (len(text)>limit or offset):
            result[key]=text[offset:offset+limit]
            metadata[key]={'offset':offset,'total_chars':len(text),'returned_chars':len(result[key]),
                           'next_offset':offset+limit if offset+limit<len(text) else None}
    if metadata:result['instruction_pages']=metadata
    return result


def platform_page(values,offset=0,limit=8000):
    rules=page(values['rules'],offset,limit)
    metadata=rules.pop('instruction_pages',None)
    return {**values,'rules':rules,**({'instruction_pages':metadata} if metadata else {})}
