import {useCallback,useEffect,useRef,useState} from 'react';
import {requestId} from './request-id';
export {requestId} from './request-id';

export class ApiError extends Error{constructor(message:string,public code:string,public status:number){super(message);}}
export async function api<T=any>(path:string,method='GET',body?:unknown):Promise<T>{
  const response=await fetch('/api'+path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json','X-MythWeaver':'1','Idempotency-Key':requestId()},body:body===undefined?undefined:JSON.stringify(body)});
  let data:any;
  try{data=await response.json();}catch{throw new ApiError('服务器暂时无法响应，请稍后重试','BAD_RESPONSE',response.status);}
  if(!response.ok){
    const detail=Array.isArray(data.detail)?data.detail.map((e:any)=>`${e.loc?.slice(1).join('.')||'输入'}：${e.msg}`).join('；'):data.detail;
    throw new ApiError(data.message||detail||'请求失败',data.code||'REQUEST_FAILED',response.status);
  }
  return data;
}
export function useResource<T>(path:string|null,interval=0){
  const previousPath=useRef(path);
  const [data,setData]=useState<T|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(true),[epoch,setEpoch]=useState(0);
  const refresh=useCallback(()=>setEpoch(e=>e+1),[]);
  useEffect(()=>{
    let alive=true;
    if(previousPath.current!==path){setData(null);setError('');previousPath.current=path;}
    if(!path){setData(null);setLoading(false);return;}
    setLoading(true);
    async function fetchData(){try{const next=await api<T>(path!);if(alive){setData(next);setError('');}}catch(e){if(alive)setError((e as Error).message);}finally{if(alive)setLoading(false);}}
    void fetchData();const timer=interval?setInterval(fetchData,interval):null;
    return()=>{alive=false;if(timer)clearInterval(timer);};
  },[path,epoch,interval]);
  return {data,error,loading,refresh};
}
export const pretty=(v:unknown)=>JSON.stringify(v,null,2);
export const time=(value:string)=>new Date(value).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Hong_Kong'});
export const shortTime=(value:string)=>new Date(value).toLocaleTimeString('zh-CN',{hour12:false,timeZone:'Asia/Hong_Kong',hour:'2-digit',minute:'2-digit'});
export const download=(value:unknown,name:string)=>{const url=URL.createObjectURL(new Blob([pretty(value)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download=name;link.click();URL.revokeObjectURL(url);};
export const modes:Record<string,string>={manual:'人工决定',record:'直接记录',fixed:'预设结果',rule:'执行数据规则',flow:'启动自定义流程'};
export const stateLabels:Record<string,string>={awaiting_approval:'待批准',approved:'已批准',budget_exhausted:'待继续',installing:'安装中',testing:'测试中',ready:'可用',draft:'草稿',environment_missing:'环境待重建',pending:'待决定',waiting:'等待管理员',completed:'已完成',superseded:'已被恢复替代',prepared:'待执行',applied:'已完成',answered:'已回复',rejected:'未采用',stale:'已过时',failed:'失败',unknown:'结果不确定',sent:'已送达',queued:'排队中',sending:'发送中',running:'进行中',cancelled:'已取消',interrupted:'已中断',processed:'已处理'};

export const eventLabel=(kind:string)=>({"definition.migrate":"迁移游戏框架","entity.migrated":"迁移记录数据","entity.retired":"退役记录","entity.restored":"恢复退役记录","submission.revise":"修订参与事项","flow.redirect":"管理员接管流程","policy.updated":"修改游戏设置","participant.updated":"修改参与者","channel.bind":"绑定群","channel.unbind":"解除群绑定","blueprint.save":"保存定义模板","game.clone":"复制游戏框架","flow.started":"发起流程","flow.waiting":"流程等待决定","flow.decided":"确认流程决定","flow.completed":"流程完成","flow.cancelled":"停止流程","flow.stale":"流程失效","game.created":"创建游戏","game.updated":"修改游戏信息","definition.changed":"修改游戏定义","entity.create":"新增记录","entity.update":"更新记录","submission.created":"接收参与内容","submission.decided":"保存管理员决定","rule.effect":"执行数据变更","checkpoint.created":"创建检查点","checkpoint.restored":"恢复游戏检查点","agent.applied":"执行助手计划"}[kind]||kind);
