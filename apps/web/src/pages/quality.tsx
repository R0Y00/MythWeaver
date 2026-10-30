import {useRef,useState} from 'react';
import {Plus,Play,RefreshCw,Send,Trash2} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {api,pretty,requestId,useResource} from '../api';
import type {GamePageProps} from '../types';
import {Alert,Badge,Empty,Modal,PageTitle,Panel,Tabs} from '../ui';
import './quality.css';

const statuses:Record<string,string>={planned:'待实现',implemented:'已关联实现',manual:'由GM裁决',deferred:'暂缓',out_of_scope:'不在范围'};
function Value({value}:{value:any}){return typeof value==='object'?<details><summary>{Array.isArray(value)?`${value.length} 项内容`:'查看内容'}</summary><pre>{pretty(value)}</pre></details>:<>{String(value??'未填写')}</>;}

export function QualityPage({snap,act,busy,navigate}:GamePageProps){
  const base=`/games/${snap.game.id}`;
  const docs=useResource<any>(base+'/requirements'),labs=useResource<any>(base+'/trials');
  const [tab,setTab]=useState('coverage'),[docKey,setDocKey]=useState(''),[labId,setLabId]=useState('');
  const [edit,setEdit]=useState(false),[title,setTitle]=useState(''),[source,setSource]=useState('');
  const [creating,setCreating]=useState(false),[label,setLabel]=useState('隔离试玩'),[external,setExternal]=useState(false);
  const [actor,setActor]=useState(''),[audience,setAudience]=useState('private'),[prompt,setPrompt]=useState('');
  const [testExternal,setTestExternal]=useState(false);
  const [historical,setHistorical]=useState<any>(null);
  const retry=useRef<{scope:string;text:string;key:string}|null>(null);
  const doc=docs.data?.documents?.find((d:any)=>d.key===docKey)||docs.data?.documents?.[0];
  const lab=labs.data?.trials?.find((d:any)=>d.id===labId)||labs.data?.trials?.[0];
  const inputActor='qq:'+actor.replace(/^qq:/,'');
  return <><PageTitle title="规则与试玩" description="保留原文、逐条核对实现，用隔离数据连续试玩，再检查实际结果。" action={<button className="secondary" onClick={()=>{docs.refresh();labs.refresh();}}><RefreshCw size={15}/>刷新</button>}/>
    <Tabs value={tab} onChange={setTab} items={[{key:'coverage',label:'规则覆盖与验收'},{key:'trial',label:'隔离试玩'}]}/>
    {(docs.error||labs.error)&&<Alert tone="danger">{docs.error||labs.error}</Alert>}
    {tab==='coverage'?<>
      <Panel title="业务说明" description="先保存完整规则；管理助手负责关联可执行规则、准备测试和补齐缺口。" action={<button className="primary" onClick={()=>{setTitle('');setSource('');setEdit(true);}}><Plus size={15}/>保存新说明</button>}>
        {docs.data?.documents?.length?<label>选择说明<select value={doc?.key||''} onChange={e=>setDocKey(e.target.value)}>{docs.data.documents.map((d:any)=><option key={d.key} value={d.key}>{d.content.title} · 第 {d.revision} 版</option>)}</select></label>:<Empty title="还没有业务说明" text="可以保存原文，也可以直接让管理助手导入并逐条落实。" action={<button className="secondary" onClick={()=>navigate('agent')}>去管理助手</button>}/>}
      </Panel>
      {doc&&<><Panel title={doc.content.title} description={`原文第 ${doc.revision} 版 · 历史版本永久保留`} action={<Badge state={doc.acceptance.ready?'applied':'pending'}>{doc.acceptance.ready?'已登记范围验收通过':'仍有待完成内容'}</Badge>}>
        <details><summary>查看完整原文与版本记录</summary><p className="quality-source">{doc.content.source}</p><div className="button-row">{doc.revisions.map((r:any)=><button key={r.id} className="text-button" disabled={busy} onClick={()=>void act(async()=>setHistorical(await api(base+`/requirements?key=${doc.key}&revision=${r.revision}`)))}>查看第 {r.revision} 版</button>)}</div></details>
        {!!doc.unmapped_source?.length&&<Alert tone="warning">还有原文未关联到条款：<ul>{doc.unmapped_source.map((text:string,i:number)=><li key={i}>{text}</li>)}</ul></Alert>}
        <div className="table-wrap"><table><thead><tr><th>原文条款</th><th>处理方式</th><th>实现与测试</th></tr></thead><tbody>{doc.coverage.map((c:any)=><tr key={c.key}><td><p className="quality-clause">{c.text}</p>{c.note&&<small>{c.note}</small>}</td><td>{statuses[c.status]||c.status}</td><td><Badge state={c.verified?'applied':'pending'}>{c.verified?'当前用例通过':c.linked?'已关联，待验证':'尚未关联有效实现'}</Badge><p className="muted">{c.implementations.map((r:any)=>snap.game.definition[r.section as keyof typeof snap.game.definition]?.[r.key]?.label||r.key).join('、')||'等待管理助手落实'}</p><small>{c.scenarios.length} 个验收场景</small>{c.verification_issues?.map((reason:string,i:number)=><p key={i} className="muted">{reason}</p>)}</td></tr>)}</tbody></table></div>
        <p className="muted">通过只代表已登记条款的用例和当前配置一致，仍需 GM 检查原文含义及测试是否充分。</p>
      </Panel><Panel title="整份说明的验收" description="逐个场景调用真实玩家模型，检查资源、确认、拒绝及隐私。正式游戏数据最后回滚，不发送 QQ。">
        {!doc.acceptance.current&&<Alert tone="warning">还没有当前版本的有效验收报告。修改规则、说明或启用插件后需要重新测试。</Alert>}
        <label className="quality-check"><input type="checkbox" checked={testExternal} onChange={e=>setTestExternal(e.target.checked)}/>允许本次测试运行已安装的可信插件（其文件和网络副作用不能回滚）</label>
        <button className="primary" disabled={busy||!doc.content.scenarios.length} onClick={()=>void act(async()=>{await api(base+'/acceptance','POST',{key:doc.key,revision:doc.revision,allow_external:testExternal});docs.refresh();},'业务验收已完成，请查看每项结果')}><Play size={15}/>运行 {doc.content.scenarios.length} 个验收场景</button>
        {!doc.content.scenarios.length&&<p>尚未准备测试。让管理助手为这些条款创建正常、边界和不应执行的用例。</p>}
        {Object.entries(doc.acceptance.scenarios||{}).map(([key,result]:[string,any])=><details className="quality-test" key={key}><summary>{result.passed?'✓':'✕'} {doc.content.scenarios.find((c:any)=>c.key===key)?.label||key}</summary>{result.steps.map((s:any,i:number)=><div className="quality-test-step" key={i}><strong>{s.ok?'✓':'✕'} 玩家：{s.message}</strong><p>{s.answer||s.message_detail}</p><small>{Object.entries(s.checks||{}).map(([k,v])=>`${v?'✓':'✕'} ${k==='action'?'行动状态':k==='state_changed'?'状态变化':k.startsWith('record_')?'记录字段':k}`).join(' · ')}</small></div>)}</details>)}
      </Panel></>}
    </>:<>
      <Panel title="持续隔离试玩" description="复制当前游戏状态进行测试。每轮保存真实结果，刷新页面仍可继续，正式数据保持不变。" action={<button className="primary" onClick={()=>setCreating(true)}><Plus size={15}/>新建试玩</button>}>
        {labs.data?.trials?.length?<label>选择试玩空间<select disabled={busy} value={lab?.id||''} onChange={e=>{setLabId(e.target.value);retry.current=null;}}>{labs.data.trials.map((l:any)=><option key={l.id} value={l.id}>{l.label} · {l.turns.length} 轮</option>)}</select></label>:<Empty title="创建一个试玩空间" text="在隔离环境里试错，查看确认和结算是否按你的规则运行。"/>}
      </Panel>
      {lab&&<><Alert>{lab.allow_external?'此试玩允许运行可信插件。数据库隔离，插件文件和网络副作用不在隔离保证内。':'此试玩默认禁止宿主插件执行。纯规则可以直接测试；需要插件的玩法请新建时明确允许。'} 不发送 QQ 消息；如果正式规则变了，请新建空间。</Alert>
        <Panel title={lab.label} action={<div className="button-row"><button disabled={busy} className="secondary" onClick={()=>void act(async()=>{await api(base+'/trials/change','POST',{id:lab.id,version:lab.version,action:'reset'});labs.refresh();retry.current=null;},'试玩已回到初始状态，正式数据未改变')}>重置试玩</button><button disabled={busy} className="text-button" onClick={()=>void act(async()=>{await api(base+'/trials/change','POST',{id:lab.id,version:lab.version,action:'discard'});labs.refresh();},'试玩空间已丢弃')}><Trash2 size={14}/>丢弃</button></div>}>
          <div className="two-col"><label>玩家QQ号<input value={actor} disabled={busy} onChange={e=>setActor(e.target.value.replace(/^qq:/,''))} placeholder="输入你授权测试的玩家QQ号"/></label><label>参与场景<select value={audience} disabled={busy} onChange={e=>setAudience(e.target.value)}><option value="private">私聊</option><option value="group">群聊（仅公开资料）</option></select></label></div>
          <div className="quality-chat" role="log" aria-label="隔离试玩对话">{!lab.turns.length&&<p className="muted">说一句话开始。这是真实玩家模型，会按这个空间里保存的状态执行。</p>}{lab.turns.map((t:any,i:number)=><div className="quality-chat-turn" key={i}><p className="quality-player"><small>{t.actor} · {t.audience==='group'?'群聊':'私聊'}</small><br/>{t.prompt}</p><div className="quality-assistant"><ReactMarkdown remarkPlugins={[remarkGfm]}>{t.result.answer}</ReactMarkdown></div></div>)}</div>
          <form onSubmit={e=>{e.preventDefault();if(!prompt.trim()||busy||!/^qq:[0-9]{1,25}$/.test(inputActor))return;
            const scope=[lab.id,inputActor,audience].join(':'),text=prompt.trim();if(retry.current?.scope!==scope||retry.current.text!==text)retry.current={scope,text,key:requestId()};
            void act(async()=>{await api(base+'/trials/turn','POST',{id:lab.id,version:lab.version,actor:inputActor,audience,prompt:text,key:retry.current!.key});setPrompt('');retry.current=null;labs.refresh();});
          }}><label>玩家想说的话<textarea rows={3} value={prompt} disabled={busy} onChange={e=>setPrompt(e.target.value)} placeholder="像在 QQ 里一样说话…"/></label><button className="primary" disabled={busy||!prompt.trim()||!/^qq:[0-9]{1,25}$/.test(inputActor)}><Send size={15}/>{busy?'正在处理…':'发送到试玩'}</button></form>
        </Panel><Panel title="试玩产生的变化" description="与创建时的状态比较；不会自动覆盖正式数据。">{lab.changes.length?<div className="table-wrap"><table><thead><tr><th>资料</th><th>字段</th><th>初始值</th><th>试玩后</th></tr></thead><tbody>{lab.changes.flatMap((r:any)=>Object.keys(r.after).filter(k=>JSON.stringify(r.before?.[k])!==JSON.stringify(r.after[k])).map(k=><tr key={r.id+k}><td>{snap.game.definition.types[r.type_key]?.label||r.type_key}</td><td>{snap.game.definition.types[r.type_key]?.schema?.properties?.[k]?.title||k}</td><td><Value value={r.before?.[k]}/></td><td><Value value={r.after[k]}/></td></tr>))}</tbody></table></div>:<p className="muted">尚无数据变化；对话、取消或等待确认不应扣除资源。</p>}</Panel></>}
    </>}
    {historical&&<Modal title={`${historical.content.title} · 第 ${historical.revision} 版`} onClose={()=>setHistorical(null)} wide><p className="quality-source">{historical.content.source}</p><h3>当时登记的条款</h3>{historical.coverage.map((c:any)=><p key={c.key}>{statuses[c.status]}：{c.text}</p>)}<Alert>历史版本只供核对，不覆盖当前规则。修订请告诉管理助手要保留或修改哪些条款。</Alert></Modal>}
    {edit&&<Modal title="保存完整业务说明" onClose={()=>setEdit(false)} wide><form onSubmit={e=>{e.preventDefault();void act(async()=>{const key='rules_'+requestId().replace(/-/g,'').slice(0,16);await api(base+'/requirements','PUT',{key,revision:0,title,source,clauses:source.split(/\n/).filter(p=>p.trim()).map((text,i)=>({key:'clause_'+i,text,status:'planned',implementations:[],scenarios:[],note:''})),scenarios:[]});setDocKey(key);setEdit(false);docs.refresh();},'原文已保存为待落实条款；可交给管理助手继续实现和测试');}}><label>说明名称<input required value={title} onChange={e=>setTitle(e.target.value)} maxLength={120}/></label><label>完整原文<textarea required rows={14} value={source} onChange={e=>setSource(e.target.value)} placeholder="粘贴完整工作说明和规则，不必转换成技术格式。"/></label><Alert>保存后先标记为“待实现”，不会把文字自动当成已经可执行的规则。</Alert><button className="primary" disabled={busy}>保存原文并建立待办</button></form></Modal>}
    {creating&&<Modal title="新建隔离试玩" onClose={()=>setCreating(false)}><form onSubmit={e=>{e.preventDefault();void act(async()=>{const created=await api(base+'/trials','POST',{label,allow_external:external});setLabId(created.id);setCreating(false);labs.refresh();},'隔离试玩已创建，正式数据不变');}}><label>试玩名称<input value={label} required onChange={e=>setLabel(e.target.value)} maxLength={120}/></label><label className="quality-check"><input type="checkbox" checked={external} onChange={e=>setExternal(e.target.checked)}/>允许运行可信插件</label><Alert tone={external?'warning':'info'}>{external?'插件将真实执行，可能操作文件或访问网络；这些外部副作用不能回滚。请只测试你信任的插件。':'默认只测试平台规则，不运行宿主插件，不发送QQ。'}</Alert><button className="primary" disabled={busy}>创建隔离空间</button></form></Modal>}
  </>;
}
