import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

// Render actual UI states without starting the platform or opening a browser.
const server=await createServer({root:fileURLToPath(new URL('..',import.meta.url)),optimizeDeps:{noDiscovery:true,include:[],entries:[]},server:{middlewareMode:true},appType:'custom'});
try{
  const {AgentProgress,executionKind,tokenTotals}=await server.ssrLoadModule('/src/pages/agent-progress.tsx');
  const base={id:'test',status:'running',tool_trace:[],created_at:new Date().toISOString()};
  const props={label:name=>name,resume(){},stop(){},settings(){},resuming:false,canResume:true,step:13,executions:[]};
  const render=(run,extra={})=>renderToStaticMarkup(createElement(AgentProgress,{...props,...extra,run:{...base,...run}}));
  const waiting=render({tool_trace:[{tool:'model.request',at:new Date().toISOString()}]});
  assert.ok(waiting.includes('正在等待模型生成下一步')&&waiting.includes('尚未收到下一步操作'));
  const interrupted=render({status:'failed',error_code:'MODEL_PROTOCOL_STALLED',tool_trace:[{tool:'model.response',max_tokens:4096,usage:{completion_tokens:4096}},{tool:'model.protocol',code:'MODEL_OUTPUT_TRUNCATED',attempt:3,retrying:false,executed:false}]});
  for(const text of ['任务中断，进度已保存','模型输出达到长度上限','从保存的进度继续','4096','此次无效响应没有执行任何操作'])assert.ok(interrupted.includes(text),text);
  assert.ok(!interrupted.includes('检查通过'));
  const pending=render({status:'awaiting_approval'},{executions:[{name:'execute_commands',status:'awaiting_approval'}]});
  assert.ok(pending.includes('有一项操作等待批准')&&!pending.includes('任务已验收'));
  const unknown=render({status:'failed',error_code:'MODEL_CONNECTION_ERROR'},{executions:[{name:'plugin_install',status:'unknown'}]});
  assert.ok(unknown.includes('平台不会自动重做')&&!unknown.includes('从保存的进度继续'));
  const old=render({status:'failed',error_code:'MODEL_PROTOCOL_STALLED'});
  assert.ok(old.includes('模型多次返回无法执行的内容'));
  const retry=render({tool_trace:[{tool:'model.retry',code:'MODEL_REQUEST_TIMEOUT',attempt:1,retrying:true}]});
  assert.ok(retry.includes('正在自动恢复')&&retry.includes('无需发送“继续”'));
  const auth=render({status:'failed',error_code:'MODEL_HTTP_ERROR',tool_trace:[{tool:'model.retry',code:'MODEL_HTTP_ERROR',http_status:401,attempt:1,retrying:false}]},{initialTab:'diagnostics'});
  assert.ok(auth.includes('HTTP 401')&&auth.includes('已停止自动重试'));
  assert.equal(executionKind({name:'workspace_call',arguments:{tool:'query_records'}}),'read');
  assert.equal(executionKind({name:'workspace_call',arguments:{tool:'execute_commands'}}),'change');
  assert.equal(executionKind({name:'patch_module'}),'change');
  assert.equal(executionKind({name:'read_tool_result'}),'read');
  const totals=tokenTotals([{tool:'model.response',usage:{prompt_tokens:1000,completion_tokens:100,prompt_cache_hit_tokens:800}},{tool:'model.response',usage:{prompt_tokens:1500,completion_tokens:80,prompt_tokens_details:{cached_tokens:1000}}}]);
  assert.deepEqual(totals,{input:2500,output:180,cached:1800,reported:2,cachedReported:true});
  const usage=render({status:'completed',tool_trace:[{tool:'model.response',usage:{prompt_tokens:1000,completion_tokens:100,prompt_cache_hit_tokens:800}}]},{initialTab:'diagnostics'});
  assert.ok(usage.includes('累计输入')&&usage.includes('1,000')&&usage.includes('800')&&usage.includes('缓存包含在输入中'));
  const progress=render({status:'running'},{executions:[{id:'p',name:'record_progress',status:'completed',result:{ok:true,value:{completed:'插件就绪',next_step:'连续试玩'}}}]});
  assert.ok(progress.includes('插件就绪')&&progress.includes('连续试玩'));
  const {copyText}=await server.ssrLoadModule('/src/clipboard.ts');
  let saved='',legacyCopied=false;
  Object.defineProperty(globalThis,'navigator',{value:{clipboard:{writeText:async text=>{saved=text;}}},configurable:true});
  await copyText('诊断');assert.equal(saved,'诊断');
  Object.defineProperty(globalThis,'navigator',{value:{},configurable:true});
  const area={value:'',style:{},setAttribute(){},focus(){},select(){},remove(){}};
  globalThis.document={activeElement:null,getSelection(){return null;},createElement(){return area;},body:{append(){}},execCommand(command){legacyCopied=command==='copy';return true;}};
  await copyText('HTTP 诊断');assert.ok(legacyCopied&&area.value==='HTTP 诊断');
  document.execCommand=()=>false;await assert.rejects(copyText('诊断'));
  console.log('Agent UI: 17 state, usage and clipboard checks passed.');
}finally{await server.close();}
