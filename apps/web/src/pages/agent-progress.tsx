import {useEffect,useState} from 'react';
import {AlertCircle,Check,ChevronRight,LoaderCircle,Square,Undo2} from 'lucide-react';
import type {Run} from '../types';
import {copyText} from '../clipboard';
import './agent-progress.css';

export const readTools=new Set(['inspect_runtime','inspect_capabilities','query_records','query_events','inspect_flow','plugin_list','plugin_inspect','list_controls','workspace_list','workspace_inspect','platform_rules_read','inspect_player_agent','requirements_read','trial_list','read_tool_result']);
const checks=new Set(['finish_task','simulate_actions','simulate_conversation','acceptance_run','validate_module','preview_module_patch']);
const writes=new Set(['execute_commands','plugin_draft','plugin_install','plugin_state','platform_rules_update','workspace_create','workspace_delete','trial_create','trial_turn','trial_change','patch_module']);
export function executionKind(e:any){
  const name=e.name==='workspace_call'?e.arguments?.tool:e.name;
  return readTools.has(name)?'read':checks.has(name)?'check':writes.has(name)?'change':'other';
}
const reasons:Record<string,string>={
  MODEL_OUTPUT_TRUNCATED:'模型输出达到长度上限，返回的内容不完整',
  INVALID_TOOL_INPUT:'模型返回的操作参数无法解析或存在歧义',
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

export function tokenTotals(trace:Run['tool_trace']){
  const responses=trace.filter(e=>e.tool==='model.response');
  const sums={input:0,output:0,cached:0,reported:0,cachedReported:false};
  for(const event of responses){
    const usage=event.usage;if(!usage)continue;
    if(typeof usage.prompt_tokens==='number'){sums.input+=usage.prompt_tokens;sums.reported++;}
    if(typeof usage.completion_tokens==='number')sums.output+=usage.completion_tokens;
    const cached=usage.prompt_cache_hit_tokens??usage.prompt_tokens_details?.cached_tokens;
    if(typeof cached==='number'){sums.cached+=cached;sums.cachedReported=true;}
  }
  return sums;
}

export function AgentProgress({run,executions,label,resume,resuming,canResume,stop,settings,step,initialTab='progress'}:{run:Run;executions:any[];label:(name:string)=>string;resume:()=>void;resuming:boolean;canResume:boolean;stop:()=>void;settings:()=>void;step?:number;initialTab?:string}){
  const [now,setNow]=useState(Date.now()),[tab,setTab]=useState(initialTab),[copyFallback,setCopyFallback]=useState(''),[copied,setCopied]=useState(false);
  useEffect(()=>{if(run.status!=='running')return;setNow(Date.now());const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[run.status]);
  const trace=run.tool_trace||[],events=trace.filter(e=>['model.protocol','model.retry','model.handoff'].includes(e.tool));
  const last=trace[trace.length-1],problem=events[events.length-1],working=executions.find(e=>e.status==='running'),waiting=executions.find(e=>e.status==='awaiting_approval');
  const successful=executions.filter(e=>e.status==='completed'&&e.result?.ok),changes=successful.filter(e=>executionKind(e)==='change'),checksDone=successful.filter(e=>executionKind(e)==='check');
  const failed=!!run.error_code,running=run.status==='running',unknown=executions.some(e=>e.status==='unknown');
  const lastFailure=failed&&problem&&(['MODEL_PROTOCOL_STALLED','PLAYER_HANDOFF_REQUIRED',problem.code].includes(run.error_code!))?problem:undefined;
  const response=[...trace].reverse().find(e=>e.tool==='model.response'),usage=tokenTotals(trace);
  const progress=[...successful].reverse().find(e=>e.name==='record_progress')?.result?.value;
  const title=failed?(run.status==='cancelled'?'任务已停止':'任务中断，进度已保存'):waiting?'有一项操作等待批准':run.status==='awaiting_input'?'需要你决定一个业务问题':running?(working?`正在${label(working.name)}`:last?.retrying?'正在自动恢复':last?.tool==='model.request'?'正在等待模型生成下一步':'正在整理下一步'):run.status==='completed'?'任务已验收':run.status==='budget_exhausted'?'本轮执行预算已用完':'本轮已回复';
  const reason=lastFailure?failureReason(lastFailure.code):failureReason(run.error_code);
  const needsModelSettings=['MODEL_NOT_CONFIGURED','KEY_UNREADABLE','MODEL_HTTP_ERROR','MODEL_REQUEST_TIMEOUT','MODEL_CONNECTION_ERROR'].includes(run.error_code||'')||lastFailure?.code==='MODEL_OUTPUT_TRUNCATED';
  const waitingSeconds=last?.at?Math.max(0,Math.floor((now-Date.parse(last.at))/1000)):null;
  const failedSteps=executions.filter(e=>e.status==='completed'&&e.result?.ok===false);
  const fmt=(n:number)=>n.toLocaleString('zh-CN');
  async function copyDiagnosis(){
    const text=JSON.stringify({task_id:run.id,status:run.status,error_code:run.error_code,model_requests:step,successful_changes:changes.length,completed_checks:checksDone.length,usage,recovery_events:events.map(e=>({code:e.code,at:e.at,attempt:e.attempt,retrying:e.retrying,http_status:e.http_status,details:e.details})),output_budget:response?.max_tokens},null,2);
    try{await copyText(text);setCopied(true);setCopyFallback('');}catch{setCopyFallback(text);}
  }
  function describe(e:any){
    const value=e.result?.value||{},args=e.arguments||{};
    if(e.name==='plugin_draft')return '源码已保存；安装、启用状态以安装回执为准';
    if(e.name==='plugin_install')return `${value.report?.tests?.length||0} 项插件测试 · ${value.enabled?'已启用':'未启用'}`;
    if(e.name==='patch_module')return `${args.key} · ${value.changes?.length||0} 处局部修改 · ${args.explanation}`;
    if(e.name==='execute_commands')return args.explanation||`${value.results?.length||0} 项操作已提交`;
    if(e.name==='simulate_conversation')return `${value.steps?.length||0} 轮玩家聊天 · ${value.passed?'通过':'存在失败或尚未设置检查'}`;
    if(e.name==='finish_task')return `${value.checks?.length||0} 项实际回执已核对`;
    if(e.name==='preview_module_patch')return '局部改动校验完成，尚未提交';
    return e.result?.ok===false?e.result.message||e.result.code:value.notice||'实际回执已保存';
  }
  const rows=(items:any[])=>items.length?<div className="mw-task-rows">{items.map(e=><div key={e.id||e.call_id} className="mw-task-row"><span className="mw-task-row-dot"/><div><strong>{label(e.name)}</strong><p>{describe(e)}</p></div></div>)}</div>:<p className="mw-task-empty">{tab==='changes'?'本次尚无成功的修改操作。':'本次尚无检查回执；不能据此认定任务完成。'}</p>;
  return <section className={`mw-task-status ${failed?'interrupted':running?'active':'settled'}`} aria-label="任务进度">
    <div className="mw-task-status-title"><span className="mw-task-icon">{running?<LoaderCircle size={18} className="mw-spin"/>:failed?<AlertCircle size={18}/>:<Check size={18}/>}</span><div><strong role="status">{title}</strong><small>{running&&last?.tool==='model.request'?`请求已发出${waitingSeconds!==null?`，已等待 ${waitingSeconds} 秒`:''}。尚未收到下一步操作。`:run.status==='completed'?'以实际验收回执为准':`${changes.length} 项修改操作成功 · ${checksDone.length} 次检查完成`}</small></div>{running&&<button onClick={stop}><Square size={13}/>停止</button>}</div>
    <div className="mw-task-tabs" role="tablist" aria-label="任务信息">{[['progress','进展'],['changes',`已生效修改 ${changes.length}`],['checks',`检查与验收 ${checksDone.length}`],['diagnostics','用量与诊断']].map(([id,text],index)=><button key={id} role="tab" id={`${run.id}-${id}-tab`} tabIndex={tab===id?0:-1} aria-selected={tab===id} aria-controls={`${run.id}-task-panel`} onClick={()=>setTab(id)} onKeyDown={e=>{if(e.key==='ArrowRight'||e.key==='ArrowLeft'){e.preventDefault();const ids=['progress','changes','checks','diagnostics'],next=ids[(index+(e.key==='ArrowRight'?1:3))%4];setTab(next);document.getElementById(`${run.id}-${next}-tab`)?.focus();}}}>{text}</button>)}</div>
    <div className="mw-task-panel" id={`${run.id}-task-panel`} role="tabpanel" aria-labelledby={`${run.id}-${tab}-tab`}>
      {tab==='progress'&&<>
        {failed&&<div className="mw-task-diagnosis"><strong>{reason}{lastFailure?.http_status?`（HTTP ${lastFailure.http_status}）`:''}</strong><p>{unknown?'有操作结果不确定，需先核对，平台不会自动重做。':lastFailure?.executed===false?'此次无效响应没有执行任何操作。已完成的步骤仍然保留。':'已保存执行记录；继续时核实当前状态。'}{lastFailure?.code==='MODEL_OUTPUT_TRUNCATED'?'已要求拆分操作，仍收到不完整内容。':''}</p>{lastFailure?.code==='MODEL_OUTPUT_TRUNCATED'&&response?.max_tokens&&<p>本次输出上限：{response.max_tokens} tokens。</p>}<div>{canResume&&!unknown&&<button className="mw-task-primary" onClick={resume} disabled={resuming}><Undo2 size={14}/>{resuming?'正在恢复…':'从保存的进度继续'}</button>}{needsModelSettings&&<button onClick={settings}>检查模型设置<ChevronRight size={14}/></button>}<button onClick={()=>setTab('diagnostics')}>查看具体诊断</button></div></div>}
        {running&&last?.retrying&&<p className="mw-task-note">{failureReason(last.code)}。正在第 {last.attempt} 次自动恢复，无需发送“继续”。</p>}
        {progress&&<div className="mw-task-plan"><div><small>已完成 / 阶段记录</small><p>{progress.completed}</p></div>{run.status!=='completed'&&<div><small>接下来</small><p>{progress.next_step}</p></div>}</div>}
        {!running&&run.status!=='completed'&&changes.length>0&&<p className="mw-task-note">已保存的源码、配置或数据修改见“已生效修改”；整个任务尚未通过最终验收。</p>}
        {!progress&&successful.some(e=>executionKind(e)!=='read'&&e.name!=='record_progress')&&rows(successful.filter(e=>executionKind(e)!=='read'&&e.name!=='record_progress').slice(-3))}
        {failedSteps.length>0&&<button className="mw-task-warning-link" onClick={()=>setTab('diagnostics')}>{failedSteps.length} 次操作未成功 · 查看原因<ChevronRight size={13}/></button>}
      </>}
      {tab==='changes'&&rows(changes)}
      {tab==='checks'&&<><p className="mw-task-note">结构校验、插件测试与玩家聊天是不同检查。整项任务是否完成，以最终验收为准。</p>{rows(checksDone)}</>}
      {tab==='diagnostics'&&<>
        <div className="mw-token-grid"><div><small>累计输入</small><strong>{usage.reported?fmt(usage.input):'未报告'}</strong></div><div><small>累计输出</small><strong>{usage.reported?fmt(usage.output):'未报告'}</strong></div><div><small>其中缓存命中</small><strong>{usage.cachedReported?fmt(usage.cached):'未报告'}</strong></div></div>
        <p className="mw-task-note">{step??'—'} 次模型请求 · {successful.filter(e=>executionKind(e)==='read').length} 次资料查询。Token 按服务商实际回执累计；缓存包含在输入中，不是额外用量。</p>
        {failedSteps.length>0&&<div className="mw-task-warnings"><h4>未成功的操作</h4>{failedSteps.map(e=><p key={e.id}><strong>{label(e.name)}</strong>：{e.result.message||e.result.code}</p>)}<small>后续可能已修复，保留这些记录供核对。</small></div>}
        {events.length>0&&<div className="mw-task-recoveries"><h4>{events.length} 次恢复记录{failed?' · 含最后一次中断原因':''}</h4><ol>{events.map((e,i)=><li key={i}><span>{failureReason(e.code)}{e.http_status?`（HTTP ${e.http_status}）`:''}</span><small>{e.at?new Date(e.at).toLocaleTimeString('zh-CN'):''} · {e.retrying?'已请求重试':'已停止自动重试'}{e.executed===false?' · 未执行工具':''}</small>{e.details?.map((d,i)=><small key={i}>{label(d.tool_name||'')} · {d.argument_chars??'—'} 字符 · {d.problem}{d.line?` · 第 ${d.line} 行，第 ${d.column} 列`:''}</small>)}</li>)}</ol></div>}
        {trace.some(e=>e.tool==='model.context')&&<p className="mw-task-note">已整理较早上下文；原始回执保留，可按需续读。</p>}
        {trace.some(e=>e.tool==='model.normalized')&&<p className="mw-task-note">已兼容处理 {trace.filter(e=>e.tool==='model.normalized').length} 次完整响应格式；实际操作仍经过参数校验与审批。</p>}
        {!events.length&&!failedSteps.length&&<p className="mw-task-empty">暂无异常记录。</p>}
        <div className="mw-diagnosis-copy"><button onClick={()=>void copyDiagnosis()}>{copied?'诊断已复制':'复制问题诊断'}</button><small>仅包含状态、用量、错误与恢复记录</small></div>
        {copyFallback&&<label className="mw-copy-fallback">浏览器无法直接复制，请选中文本后按 Ctrl+C<textarea aria-label="可手动复制的问题诊断" readOnly value={copyFallback} onFocus={e=>e.target.select()} rows={6}/></label>}
      </>}
    </div>
  </section>;
}
