import {useEffect,useState} from 'react';
import {AlertCircle,Check,ChevronRight,LoaderCircle,Square,Undo2} from 'lucide-react';
import type {Run} from '../types';
import './agent-progress.css';

export const readTools=new Set(['inspect_runtime','inspect_capabilities','query_records','query_events','inspect_flow','plugin_list','plugin_inspect','list_controls','workspace_list','workspace_inspect','platform_rules_read','inspect_player_agent','requirements_read','trial_list']);
const checks=new Set(['finish_task','simulate_actions','simulate_conversation','acceptance_run','validate_module']);
const writes=new Set(['execute_commands','plugin_draft','plugin_install','plugin_state','platform_rules_update','workspace_create','workspace_delete','trial_create','trial_turn','trial_change']);
export function executionKind(e:any){
  const name=e.name==='workspace_call'?e.arguments?.tool:e.name;
  return readTools.has(name)?'read':checks.has(name)?'check':writes.has(name)?'change':'other';
}
const reasons:Record<string,string>={
  MODEL_OUTPUT_TRUNCATED:'模型输出达到长度上限，返回的内容不完整',
  INVALID_TOOL_INPUT:'模型返回的操作参数不是完整、有效的 JSON',
  INVALID_TOOL_BATCH:'模型一次返回的操作数量超过当前设置',
  EMPTY_MODEL_RESPONSE:'模型返回了空内容',INVALID_MODEL_RESPONSE:'模型服务返回了无法识别的响应',
  FINISH_MUST_BE_SINGLE_CALL:'模型把最终验收和其他操作放在同一轮',ASK_MUST_BE_SINGLE_CALL:'模型把业务提问和其他操作放在同一轮',
  MODEL_REQUEST_TIMEOUT:'等待模型响应超时',MODEL_CONNECTION_ERROR:'连接模型服务失败',MODEL_HTTP_ERROR:'模型服务拒绝请求或暂时不可用',
  PLAYER_HANDOFF_REQUIRED:'模型试图结束，但玩家助手的交接验收尚未完成',MODEL_PROTOCOL_STALLED:'模型多次返回无法执行的内容',
  MODEL_NOT_CONFIGURED:'尚未分配管理模型',KEY_UNREADABLE:'模型密钥无法解密，需要重新填写',
  PROCESS_RESTARTED:'服务器重启，中断了正在执行的任务',TASK_TIMEOUT:'任务达到配置的总时长',TASK_CANCELLED:'你已停止任务',
  MODEL_UNAVAILABLE:'模型请求未完成',RECONCILIATION_REQUIRED:'有操作的结果不确定，需要先核对实际状态',
  MODEL_RESPONSE_TOO_LARGE:'模型响应超过可接收的大小，需要拆分操作',
  EXECUTION_MODE_REQUIRED:'这项操作需要运行代码，当前旧版方案模式无法执行',FORBIDDEN_TOOL:'模型请求了当前不可用的工具，未执行该操作',
};
export function failureReason(code?:string|null){return code?(reasons[code]||`执行遇到错误（${code}）`):'';}

export function AgentProgress({run,executions,label,resume,resuming,canResume,stop,settings,step}:{run:Run;executions:any[];label:(name:string)=>string;resume:()=>void;resuming:boolean;canResume:boolean;stop:()=>void;settings:()=>void;step?:number}){
  const [now,setNow]=useState(Date.now());
  const [copied,setCopied]=useState('');
  useEffect(()=>{if(run.status!=='running')return;setNow(Date.now());const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[run.status]);
  const trace=run.tool_trace||[],events=trace.filter(e=>['model.protocol','model.retry','model.handoff'].includes(e.tool));
  const last=trace[trace.length-1],problem=events[events.length-1],working=executions.find(e=>e.status==='running'),waiting=executions.find(e=>e.status==='awaiting_approval');
  const successful=executions.filter(e=>e.status==='completed'&&e.result?.ok),changed=successful.filter(e=>executionKind(e)==='change').length;
  const tested=successful.filter(e=>executionKind(e)==='check').length,read=successful.filter(e=>executionKind(e)==='read').length;
  const failed=!!run.error_code,running=run.status==='running',unknown=executions.some(e=>e.status==='unknown');
  const lastFailure=failed&&problem&&(['MODEL_PROTOCOL_STALLED','PLAYER_HANDOFF_REQUIRED',problem.code].includes(run.error_code!))?problem:undefined;
  const response=[...trace].reverse().find(e=>e.tool==='model.response');
  const title=failed?(run.status==='cancelled'?'任务已停止':'任务中断，进度已保存'):waiting?'有一项操作等待批准':run.status==='awaiting_input'?'需要你决定一个业务问题':running?(working?`正在${label(working.name)}`:last?.retrying?'正在自动恢复':last?.tool==='model.request'?'正在等待模型生成下一步':'正在整理下一步') :run.status==='completed'?'任务已验收':run.status==='budget_exhausted'?'本轮执行预算已用完':'本轮已回复';
  const reason=lastFailure?failureReason(lastFailure.code):failureReason(run.error_code);
  const waitingSeconds=last?.at?Math.max(0,Math.floor((now-Date.parse(last.at))/1000)):null;
  const failedSteps=executions.filter(e=>e.status==='completed'&&e.result?.ok===false);
  async function copyDiagnosis(){
    const text=JSON.stringify({task_id:run.id,status:run.status,error_code:run.error_code,model_requests:step,successful_changes:changed,completed_checks:tested,recovery_events:events.map(e=>({code:e.code,at:e.at,attempt:e.attempt,retrying:e.retrying,http_status:e.http_status})),output_budget:response?.max_tokens},null,2);
    try{await navigator.clipboard.writeText(text);setCopied('诊断已复制');}catch{setCopied('浏览器无法复制，请展开恢复记录查看。');}
  }
  return <section className={`mw-task-status ${failed?'interrupted':running?'active':'settled'}`} aria-label="任务进度">
    <div className="mw-task-status-title">{running?<LoaderCircle size={18} className="mw-spin"/>:failed?<AlertCircle size={18}/>:<Check size={18}/>}<strong role="status">{title}</strong>{running&&<button onClick={stop}><Square size={13}/>停止</button>}</div>
    <div className="mw-task-stats"><span><b>{read}</b>次查询</span><span><b>{changed}</b>项修改操作成功</span><span><b>{tested}</b>次检查完成</span>{step!==undefined&&<span><b>{step}</b>轮模型</span>}</div>
    {running&&!working&&<p>{last?.retrying?`${failureReason(last.code)}。已安排第 ${last.attempt} 次恢复，无需发送“继续”。`:last?.tool==='model.request'?`请求已发出${waitingSeconds!==null?`，已等待 ${waitingSeconds} 秒`:''}。尚未收到下一步操作。`:'已保存工具回执，正在继续处理。'}</p>}
    {failed&&<div className="mw-task-diagnosis"><strong>{reason}{lastFailure?.http_status?`（HTTP ${lastFailure.http_status}）`:''}</strong><p>{lastFailure?.code==='MODEL_OUTPUT_TRUNCATED'?'已提示模型拆成更小的操作，仍收到不完整内容。可检查模型输出长度设置，再从保存的进度继续。':lastFailure?.tool==='model.retry'?'模型请求未成功。请检查模型服务与网络后继续。':'已完成的操作和回执保留；继续任务前会核实进度。'}{unknown?' 有操作结果不确定，需先核对，平台不会自动重做。':lastFailure?.executed===false?' 此次无效响应没有执行任何操作。':''}</p>{lastFailure?.code==='MODEL_OUTPUT_TRUNCATED'&&response?.max_tokens&&<p>本次输出上限：{response.max_tokens} tokens{response.usage?.completion_tokens!==undefined?`；模型报告已用：${response.usage.completion_tokens}`:''}。</p>}<div>{canResume&&!unknown&&<button className="mw-task-primary" onClick={resume} disabled={resuming}><Undo2 size={14}/>{resuming?'正在恢复…':'从保存的进度继续'}</button>}<button onClick={settings}>检查模型设置<ChevronRight size={14}/></button></div></div>}
    {!running&&run.status!=='completed'&&changed>0&&<p>上述修改已经生效，整个任务尚未通过最终验收。</p>}
    {failedSteps.length>0&&<details className="mw-task-warnings"><summary>{failedSteps.length} 次操作未成功 · 查看原因</summary>{failedSteps.map(e=><p key={e.id}><strong>{label(e.name)}</strong>：{e.result.message||e.result.code||'未成功'}（后续可能已修复，以最终验收为准）</p>)}</details>}
    {events.length>0&&<details className="mw-task-recoveries"><summary>{events.length} 次恢复记录{failed?' · 含最后一次中断原因':''}</summary><ol>{events.map((e,i)=><li key={i}><span>{failureReason(e.code)}{e.http_status?`（HTTP ${e.http_status}）`:''}</span><small>{e.at?new Date(e.at).toLocaleTimeString('zh-CN'):''} · {e.retrying?'已请求重试':'已停止自动重试'}{e.executed===false?' · 未执行工具':''}</small></li>)}</ol></details>}
    {failed&&<div className="mw-diagnosis-copy"><button onClick={()=>void copyDiagnosis()}>复制问题诊断</button><small>{copied||'仅包含状态、错误原因与恢复记录'}</small></div>}
  </section>;
}
