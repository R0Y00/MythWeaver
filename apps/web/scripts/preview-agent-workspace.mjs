// Development-only fixture. No database, provider credentials, QQ or production API.
import {createServer} from 'vite';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('..',import.meta.url));
const at=new Date().toISOString();
const execution=(id,name,value,args={})=>({id,call_id:id,name,arguments:args,status:'completed',result:{ok:true,value}});
const executions=[execution('progress','record_progress',{completed:'已保存新插件，6项插件测试通过。保留了原有品种与账号数据。',next_step:'局部更新玩法模块，再运行玩家连续对话和业务验收。'}),execution('draft','plugin_draft',{}, {key:'demo_rules'}),execution('install','plugin_install',{report:{tests:Array(6).fill({passed:true})},enabled:true}),execution('inspect','inspect_runtime',{definition_version:8}),execution('validate','validate_module',{valid:true})];
const run={id:'preview-run',game_id:'preview',kind:'harness',prompt:'方案二可以，但风险不能太大；保留超级稀有品种和我的账号。',answer:'',status:'failed',error_code:'MODEL_PROTOCOL_STALLED',created_at:at,duration_ms:260000,tool_trace:[{tool:'model.response',usage:{prompt_tokens:12400,completion_tokens:420,prompt_cache_hit_tokens:10500},max_tokens:4096},{tool:'model.protocol',code:'MODEL_OUTPUT_TRUNCATED',at,attempt:1,retrying:true,executed:false},{tool:'model.protocol',code:'INVALID_TOOL_INPUT',at,attempt:3,retrying:false,executed:false,details:[{tool_name:'execute_commands',argument_chars:8410,problem:'Unterminated string starting at',line:1,column:92}]}]};
const session={id:'preview-session',approval_mode:'auto',title:'低风险平衡调整',mode:'harness',pinned:true,archived:false,deleted:false,updated_at:at,last_status:'failed',preview:'方案二可以，但风险不能太大',run_count:4};
const fixture=`import React from 'react';import{createRoot}from'react-dom/client';import{AgentPage}from'/src/pages/assistant.tsx';import'/src/style.css';import'/src/assistant.css';localStorage.setItem('mythweaver.session.preview','preview-session');function Demo(){const[dark,setDark]=React.useState(false);return <div data-theme={dark?'dark':'light'} style={{padding:20,background:dark?'#121722':'#f5f6fa',minHeight:'100vh'}}><div style={{display:'flex',justifyContent:'space-between',marginBottom:14,color:dark?'#e0e5ef':'#334155'}}><strong>MythWeaver · 管理助手 <small style={{fontWeight:400,opacity:.6}}>隔离演示，仅虚构数据</small></strong><label><input type="checkbox" checked={dark} onChange={e=>{setDark(e.target.checked);document.documentElement.dataset.theme=e.target.checked?'dark':'light';}}/>深色模式</label></div><AgentPage snap={{game:{id:'preview',name:'万界游记'}}} status={{agent:{configured:true,model:'deepseek-flash'}}} act={async f=>f()} busy={false} navigate={()=>{}}/></div>}createRoot(document.getElementById('root')).render(<Demo/>);`;
const server=await createServer({root,server:{host:'127.0.0.1',port:Number(process.argv[2]||18121),strictPort:true},plugins:[{
  name:'isolated-agent-preview',resolveId(id){if(id==='/__agent_preview.tsx')return '\0agent-preview';},load(id){if(id==='\0agent-preview')return fixture;},
  async transform(code,id){if(id==='\0agent-preview'){const{transformWithEsbuild}=await import('vite');return transformWithEsbuild(code,'agent-preview.tsx',{loader:'tsx',jsx:'automatic'});}},
  configureServer(server){server.middlewares.use(async(req,res,next)=>{
    if(req.url==='/__agent_preview'){res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml(req.url,'<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Agent 工作区隔离预览</title><div id="root"></div><script type="module" src="/__agent_preview.tsx"></script></html>'));return;}
    if(!req.url?.startsWith('/api/'))return next();
    res.setHeader('Content-Type','application/json');
    if(req.method!=='GET'){res.statusCode=409;res.end(JSON.stringify({message:'隔离预览不执行任何修改'}));return;}
    let result={};
    if(req.url.includes('/tasks/'))result={...run,step:16,max_steps:0,executions};
    else if(req.url.includes('/sessions/preview-session'))result={session,runs:[run],has_more:false,total:1};
    else if(req.url.includes('/sessions?'))result={items:[session],has_more:false,total:1};
    res.end(JSON.stringify(result));
  });}
}]});
await server.listen();console.log(`Isolated UI fixture: http://127.0.0.1:${server.config.server.port}/__agent_preview`);
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,async()=>{await server.close();process.exit();});
