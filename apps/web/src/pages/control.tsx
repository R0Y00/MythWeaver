import React,{useEffect,useState} from 'react';
import {Check,Code2,RefreshCw,ShieldCheck,Sparkles} from 'lucide-react';
import {api,eventLabel,pretty,useResource} from '../api';
import type {GamePageProps,Plan} from '../types';
import {Alert,Badge,Empty,PageTitle,Panel,Tabs} from '../ui';

type Capability={kind:string;description:string;schema:any};
const groups:Record<string,string>={module:'运行时模块',game:'游戏空间',definition:'定义与迁移',entity:'记录管理',submit:'参与与结果',decide:'参与与结果',submission:'参与与结果',policy:'设置与权限',participant:'设置与权限',channel:'群绑定',checkpoint:'检查点与恢复',blueprint:'模板',flow:'流程接管'};

function defaultValue(schema:any,root:any):any{
  if(schema?.$ref)return defaultValue(root.$defs?.[schema.$ref.split('/').pop()],root);
  if(schema?.default!==undefined)return schema.default;
  if(schema?.type==='object')return Object.fromEntries(Object.entries(schema.properties||{}).filter(([k])=>schema.required?.includes(k)).map(([k,s])=>[k,defaultValue(s,root)]));
  if(schema?.type==='array')return [];
  if(schema?.type==='integer'||schema?.type==='number')return schema.minimum??1;
  if(schema?.type==='boolean')return false;
  return '';
}

export function ControlPage({snap,act,busy,navigate}:GamePageProps){
  const capabilities=useResource<{commands:Capability[];message:string}>(`/games/${snap.game.id}/capabilities`),retired=useResource<any[]>(`/games/${snap.game.id}/retired`,5000);
  const [tab,setTab]=useState('capabilities'),[kind,setKind]=useState('game.update'),[source,setSource]=useState(''),[plan,setPlan]=useState<Plan|null>(null),[search,setSearch]=useState('');
  const plugins=useResource<any[]>(`/games/${snap.game.id}/plugins`,5000);
  const commands=capabilities.data?.commands||[],current=commands.find(c=>c.kind===kind),rows=commands.filter(c=>(c.kind+c.description).includes(search));
  function initial(c:Capability){
    const first=snap.entities[0],submission=snap.submissions[0],flow=snap.flows[0];
    const value=defaultValue(c.schema,c.schema);
    if(c.kind==='game.update')return {name:snap.game.name,description:snap.game.description,version:snap.game.version};
    if(c.kind==='module.put')return {key:'custom_module',version:snap.game.version,manifest:{label:'由你定义的新模块',description:'说明需要创建的能力',enabled:true,requires:[],types:{},interactions:{},workflows:{},functions:{}}};
    if(c.kind==='module.state')return {key:Object.keys(snap.game.definition.modules||{})[0]||'custom_module',version:snap.game.version,enabled:false};
    if(c.kind==='module.remove')return {key:Object.keys(snap.game.definition.modules||{})[0]||'custom_module',version:snap.game.version};
    if(c.kind==='definition')return {version:snap.game.version,definition:snap.game.definition};
    if(c.kind==='definition.migrate')return {version:snap.game.version,definition:snap.game.definition,changes:[],retire:[],reason:'说明此次框架及数据迁移'};
    if(c.kind==='policy.update')return {...snap.policy,version:snap.policy_version};
    if(c.kind==='entity.update'&&first)return {id:first.id,type_key:first.type_key,data:first.data,version:first.version,visibility:first.visibility,archived:first.archived};
    if(c.kind==='entity.retire'&&first)return {id:first.id,version:first.version,reason:'说明退役原因'};
    if(c.kind==='entity.restore'&&retired.data?.[0])return {id:retired.data[0].id,version:retired.data[0].version,reason:'管理员恢复退役记录'};
    if(c.kind==='submission.revise'&&submission)return {id:submission.id,version:submission.version,interaction:submission.interaction,data:submission.data,status:submission.status,result:submission.result,reason:'说明修订原因；不重演旧副作用'};
    if(c.kind==='flow.redirect'&&flow)return {id:flow.id,version:flow.version,step:flow.current_step,input:flow.context.input,answers:{},reason:'说明接管和跳转安排'};
    if(c.kind==='checkpoint.restore')return {id:'填入检查点完整ID',version:snap.game.version};
    return value;
  }
  useEffect(()=>{if(current){setSource(pretty(initial(current)));setPlan(null);}},[kind,!!current]);
  async function preview(){const value=JSON.parse(source);setPlan(await api<Plan>(`/games/${snap.game.id}/commands/preview`,'POST',{commands:[{kind,payload:value}],explanation:current?.description||kind}));}
  return <><PageTitle title="游戏控制台" description="这里列出的游戏命令，同时对管理界面和已认证管理员 Agent 开放。" action={<button className="secondary" onClick={()=>navigate('agent')}><Sparkles size={15}/>交给管理助手</button>}/><div className="control-intro"><ShieldCheck size={22}/><div><strong>{commands.length} 项共享游戏命令</strong><p>参数校验、权限、事务、版本、审计使用同一套服务。Agent 可以按本次明确授权直接执行；平台密钥和消息发送不属于此命令集。</p></div></div><Tabs value={tab} onChange={setTab} items={[{key:'capabilities',label:'能力与操作'},{key:'plugins',label:'代码插件',count:plugins.data?.length||0},{key:'modules',label:'运行时模块',count:Object.keys(snap.game.definition.modules||{}).length},{key:'retired',label:'退役记录',count:retired.data?.length||0}]}/>{tab==='plugins'?<Panel title="代码插件与版本" description="Agent 可编写 Python/Node 插件、安装 pip/npm 依赖并测试；通过后注册工具。独立进程不是系统级安全沙箱，仅运行管理员信任的代码。">{plugins.data?.length?plugins.data.map(p=><details key={p.id}><summary>{p.manifest.label||p.key} · r{p.revision} · {p.status} · {p.enabled?'已启用':'未启用'}</summary><pre>{pretty({id:p.id,manifest:p.manifest,report:p.report})}</pre>{p.status==='ready'&&<button className="secondary" disabled={busy} onClick={()=>void act(async()=>{await api(`/games/${snap.game.id}/plugins/state`,'POST',{id:p.id,enabled:!p.enabled});plugins.refresh();},'插件版本状态已更新')}>{p.enabled?'停用此版本':'启用此版本'}</button>}</details>):<Empty title="尚无代码插件" text="在 Agent 执行模式描述要创建或安装的能力；插件源码、测试结果与版本会显示在这里。"/>}</Panel>:tab==='modules'?<Panel title="当前模块" description="由管理员和 Agent 创建，模块可以定义自己的数据、交互、流程和计算函数。">{Object.entries(snap.game.definition.modules||{}).length?Object.entries(snap.game.definition.modules||{}).map(([key,m])=><details key={key}><summary>{m.label||key} · r{m.revision} · {m.enabled===false?'已停用':'已启用'}</summary><p>{m.description}</p><pre>{pretty(m)}</pre><button className="text-button" onClick={()=>navigate('agent')}>交给管理助手修改</button></details>):<Empty title="没有预装玩法模块" text="可以直接告诉管理 Agent 想要什么功能，让它创建所需模块。"/>}</Panel>:tab==='retired'?<Panel title="可恢复的退役记录" description="退役记录保留原始数据和原因。恢复时必须符合当前定义；定义迁移前会自动保存检查点。">{retired.data?.length?<div className="table-wrap"><table><thead><tr><th>类型 / 编号</th><th>原因</th><th>版本</th><th>操作</th></tr></thead><tbody>{retired.data.map(row=><tr key={row.id}><td>{row.snapshot.type_key}<small className="record-key">{row.id}</small></td><td>{row.reason}</td><td>v{row.version}</td><td><button className="text-button" disabled={busy} onClick={()=>void act(async()=>{await api(`/games/${snap.game.id}/commands`,'POST',{kind:'entity.restore',payload:{id:row.id,version:row.version,reason:'管理员从退役区恢复'}});retired.refresh();},'记录已恢复；版本已递增')}>恢复记录</button></td></tr>)}</tbody></table></div>:<Empty title="没有退役记录" text="归档仍保留有效定义；退役将记录移出当前数据，可通过此处恢复。"/>}</Panel>:<div className="control-layout"><aside className="control-catalog"><input aria-label="搜索管理能力" placeholder="搜索能力或命令" value={search} onChange={e=>setSearch(e.target.value)}/>{rows.map(c=><button key={c.kind} className={kind===c.kind?'selected':''} onClick={()=>{setKind(c.kind);setPlan(null);}}><small>{groups[c.kind.split('.')[0]]||'管理'}</small><strong>{c.description}</strong><code>{c.kind}</code></button>)}</aside><Panel className="control-command"><div className="section-heading"><div><h3>{current?.description||'选择管理命令'}</h3><p>{kind}</p></div><button className="text-button" onClick={()=>{if(current)setSource(pretty(initial(current)));setPlan(null);}}><RefreshCw size={13}/>读取当前值</button></div><Alert>这是高级操作入口。可先让助手生成参数，或在此编辑；预演不会修改游戏，执行时会再次检查状态。</Alert><label>操作参数<textarea className="code-input" rows={15} aria-label="游戏命令参数" value={source} onChange={e=>{setSource(e.target.value);setPlan(null);}}/></label><details><summary>查看完整参数结构</summary><pre>{pretty(current?.schema||{})}</pre></details><div className="form-footer"><button className="primary" disabled={busy||!current} onClick={()=>void act(preview,'事务预演已完成，未修改游戏')}><Code2 size={14}/>预演本次操作</button></div>{plan&&<section className="control-preview"><div className="section-heading"><h3>{plan.status==='applied'?'已执行 · 预演影响':'具体影响'}</h3><Badge state={plan.status}/></div><p className="muted">{plan.status==='applied'?'操作已提交。以下保留预演影响，真实编号与返回值见“操作结果（已提交）”。':plan.impact?.notice}</p>{plan.impact?.events?.map((event:any)=><details key={event.seq}><summary>{eventLabel(event.kind)}</summary><pre>{pretty(event.payload)}</pre></details>)}<details><summary>操作结果{plan.status==='applied'?'（已提交）':'（预演）'}</summary><pre>{pretty(plan.result||plan.preview)}</pre></details>{plan.status==='prepared'&&<div className="form-footer"><button className="primary" disabled={busy} onClick={()=>void act(async()=>{setPlan(await api<Plan>(`/games/${snap.game.id}/agent/plans/${plan.id}/apply`,'POST',{}));retired.refresh();},'管理员操作已提交')}><Check size={15}/>执行本次操作</button></div>}</section>}</Panel></div>}</>;
}
