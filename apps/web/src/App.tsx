import React,{lazy,Suspense,useCallback,useEffect,useState} from 'react';
import {Activity,ArrowRight,Blocks,BookOpen,ChevronDown,Database,FolderKanban,GitBranch,History,LayoutDashboard,LogOut,MessageSquare,Monitor,Moon,Network,Plus,Search,Settings2,ShieldCheck,Sparkles,Sun,Users,X} from 'lucide-react';
import {api,useResource} from './api';
import type {Act,Game,Snapshot,Status} from './types';
import {Alert,Loading,Modal} from './ui';
import {Overview,DataPage,DefinitionPage,DecisionsPage} from './pages/workspace';
import {SettingsPage,ParticipantsPage,BlueprintsPage,HistoryPage,OperationsPage} from './pages/management';
import {ModelsPage} from './pages/ai';
import {FlowsPage} from './pages/flows';
import {ControlPage} from './pages/control';
import {PlatformRulesPage} from './pages/platform';
import {GameDeletion} from './pages/game-deletion';
const AgentPage=lazy(()=>import('./pages/assistant').then(module=>({default:module.AgentPage})));

const NAV=[{label:'工作空间',items:[{key:'overview',label:'运行概览',icon:LayoutDashboard},{key:'decisions',label:'互动工作台',icon:MessageSquare},{key:'data',label:'游戏数据',icon:Database}]},
  {label:'游戏设计',items:[{key:'definition',label:'定义与交互',icon:Blocks},{key:'flows',label:'流程与决定',icon:GitBranch},{key:'control',label:'游戏控制台',icon:ShieldCheck},{key:'settings',label:'游戏设置',icon:Settings2},{key:'participants',label:'参与者与权限',icon:Users},{key:'blueprints',label:'模板库',icon:FolderKanban}]},
  {label:'AI 与运维',items:[{key:'agent',label:'助手工作区',icon:Sparkles},{key:'models',label:'模型管理',icon:Network},{key:'platform',label:'平台规则',icon:Settings2},{key:'operations',label:'连接与诊断',icon:Activity},{key:'history',label:'历史与检查点',icon:History}]}];
const TITLES=Object.fromEntries(NAV.flatMap(group=>group.items.map(item=>[item.key,item.label])));

export default function App(){
  const [logged,setLogged]=useState<boolean|null>(null),[password,setPassword]=useState(''),[loginError,setLoginError]=useState('');
  const [page,setPage]=useState(localStorage.getItem('mw.page')||'overview'),[gameId,setGameId]=useState(localStorage.getItem('mw.game')||'');
  const [theme,setTheme]=useState(localStorage.getItem('mw.theme')||'light'),[mobileNav,setMobileNav]=useState(false);
  const [busy,setBusy]=useState(false),[toast,setToast]=useState<{text:string;error?:boolean}|null>(null);
  const [create,setCreate]=useState(false),[edit,setEdit]=useState(false),[name,setName]=useState(''),[description,setDescription]=useState('');
  const [deleting,setDeleting]=useState<Game|null>(null),[removedIds,setRemovedIds]=useState<string[]>([]);
  const [palette,setPalette]=useState(false),[query,setQuery]=useState('');
  const games=useResource<Game[]>(logged?'/games':null);
  const availableGames=games.data?.filter(game=>!removedIds.includes(game.id));
  const status=useResource<Status>(logged?'/status':null,5000);
  const snapshot=useResource<Snapshot>(logged&&gameId?'/games/'+gameId:null,5000);
  const snap=snapshot.data?.game.id===gameId?snapshot.data:null;
  const refresh=useCallback(()=>{games.refresh();status.refresh();snapshot.refresh();},[games.refresh,status.refresh,snapshot.refresh]);
  const act:Act=async(task,success)=>{setBusy(true);try{await task();refresh();if(success)setToast({text:success});return true;}catch(e){setToast({text:(e as Error).message,error:true});return false;}finally{setBusy(false);}};
  function navigate(key:string){setPage(key);setMobileNav(false);localStorage.setItem('mw.page',key);}
  useEffect(()=>{api('/status').then(()=>setLogged(true)).catch(()=>setLogged(false));},[]);
  useEffect(()=>{localStorage.setItem('mw.game',gameId);},[gameId]);
  useEffect(()=>{document.documentElement.dataset.theme=theme;localStorage.setItem('mw.theme',theme);},[theme]);
  useEffect(()=>{if(!games.loading&&games.data&&(!gameId||!games.data.some(game=>game.id===gameId&&!removedIds.includes(game.id))))setGameId(games.data.find(game=>!removedIds.includes(game.id))?.id||'');},[games.data,games.loading,gameId,removedIds]);
  useEffect(()=>{if(!toast)return;const timer=setTimeout(()=>setToast(null),toast.error?9000:4500);return()=>clearTimeout(timer);},[toast]);
  useEffect(()=>{const handler=(event:KeyboardEvent)=>{if((event.ctrlKey||event.metaKey)&&event.key==='k'){event.preventDefault();setPalette(v=>!v);}};document.addEventListener('keydown',handler);return()=>document.removeEventListener('keydown',handler);},[]);
  const pending=snap?.submissions.filter(s=>s.status==='pending').length||0;
  function newGame(){setEdit(false);setName('');setDescription('');setCreate(true);}

  if(logged===null)return <Loading/>;
  if(!logged)return <div className="login-screen"><div className="login-story"><div className="login-brand"><span className="app-logo"><Blocks size={23}/></span>MythWeaver</div><div className="story-main"><span className="overline">A WORKSPACE FOR YOUR IDEAS</span><h1>让每一种想法，<br/>都有自己的玩法。</h1><p>由你定义世界、规则和决定。<br/>在同一工作台中管理互动、数据与 AI。</p><div className="story-cards"><div><Database size={19}/><span>自由定义的数据</span></div><div><MessageSquare size={19}/><span>真人掌握的决定</span></div><div><Sparkles size={19}/><span>按需启用的助手</span></div></div></div><small>MYTHWEAVER CONSOLE / WINDOWS NATIVE</small><div className="login-decoration"/></div><div className="login-form-wrap"><form className="login-form" onSubmit={event=>{event.preventDefault();setLoginError('');setBusy(true);api('/login','POST',{password}).then(()=>{setLogged(true);setPassword('');}).catch(e=>setLoginError(e.message)).finally(()=>setBusy(false));}}><span className="mini-icon"><ShieldCheck size={24}/></span><h2>登录管理工作台</h2><p>所有游戏、配置和运行状态，都在这里。</p><label>管理员密码<input type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)} required placeholder="输入本机管理员密码"/></label>{loginError&&<Alert tone="danger">{loginError}</Alert>}<button className="primary full" disabled={busy}>进入工作台 <ArrowRight size={16}/></button><div className="login-hint"><Monitor size={15}/><span>本机运行 · 密码位于<br/><code>.runtime/credentials.txt</code></span></div></form></div></div>;

  const props=snap?{snap,act,busy,navigate}:null;
  let content:React.ReactNode;
  if(page==='models')content=<ModelsPage act={act} busy={busy}/>;
  else if(page==='blueprints')content=<BlueprintsPage key={gameId} snap={snap} act={act} busy={busy} onCreated={id=>{setGameId(id);navigate('overview');}}/>;
  else if(page==='platform')content=<PlatformRulesPage act={act} busy={busy}/>;
  else if(page==='operations')content=<OperationsPage key={gameId} snap={snap} status={status.data} act={act} busy={busy}/>;
  else if(!gameId||(!games.loading&&!availableGames?.length))content=<div className="welcome-empty"><span className="overline">YOUR FIRST WORKSPACE</span><h1>先留一片空白，<br/>再定义你的游戏。</h1><p>无需选择职业、战斗或任何预设规则。<br/>从文字互动开始，逐步加入你需要的能力。</p><button className="primary" onClick={newGame}><Plus size={16}/>创建空白游戏</button><div className="blank-flow"><span><FolderKanban/>创建空间</span><ArrowRight/><span><Blocks/>自由定义</span><ArrowRight/><span><Users/>一起参与</span></div></div>;
  else if(!props)content=<Loading/>;
  else if(page==='definition')content=<DefinitionPage key={gameId} {...props}/>;
  else if(page==='control')content=<ControlPage key={gameId} {...props}/>;
  else if(page==='flows')content=<FlowsPage key={gameId} {...props}/>;
  else if(page==='data')content=<DataPage key={gameId} {...props}/>;
  else if(page==='decisions')content=<DecisionsPage key={gameId} {...props}/>;
  else if(page==='settings')content=<SettingsPage key={gameId} {...props} onDelete={()=>setDeleting(snap!.game)} onEditInfo={()=>{setEdit(true);setName(snap!.game.name);setDescription(snap!.game.description);setCreate(true);}}/>;
  else if(page==='participants')content=<ParticipantsPage key={gameId} {...props}/>;
  else if(page==='agent')content=<AgentPage key={gameId} {...props} status={status.data}/>;
  else if(page==='history')content=<HistoryPage key={gameId} {...props}/>;
  else content=<Overview key={gameId} {...props} status={status.data}/>;

  return <div className="app-shell"><aside className={'sidebar '+(mobileNav?'open':'')}><a className="brand" href="#" onClick={e=>{e.preventDefault();navigate('overview');}}><span className="app-logo"><Blocks size={21}/></span><div>MythWeaver<small>ADMIN WORKSPACE</small></div><span className="edition">β</span></a><div className="workspace-switch"><label>当前游戏空间</label><select aria-label="选择游戏" value={gameId} onChange={e=>{setGameId(e.target.value);navigate('overview');}}><option value="">选择或创建游戏</option>{availableGames?.map(g=><option key={g.id} value={g.id}>{g.name}</option>)}</select><button className="new-workspace" onClick={newGame}><Plus size={14}/>创建空白游戏</button></div><div className="nav-groups">{NAV.map(group=><nav key={group.label}><div className="nav-label">{group.label}</div>{group.items.map(item=><button key={item.key} className={'nav-item '+(page===item.key?'active':'')} onClick={()=>navigate(item.key)}><item.icon size={17}/><span>{item.label}</span>{item.key==='decisions'&&pending>0&&<b>{pending}</b>}{item.key==='models'&&!status.data?.agent.configured&&<i className="nav-dot"/>}</button>)}</nav>)}</div><div className="sidebar-foot"><div className="local-status"><i className={status.data?.onebot.connected?'online':''}/><div>{status.data?.onebot.connected?'NapCat 已连接':'NapCat 等待连接'}<small>Windows 原生运行 · v{status.data?.version||'0.9.5'}</small></div></div><button className="profile" onClick={()=>void act(async()=>{await api('/logout','POST');setLogged(false);},'已退出')}><span className="avatar">A</span><div>管理员<small>本机完整管理权限</small></div><LogOut size={15}/></button></div></aside>
  {mobileNav&&<button className="nav-overlay" aria-label="关闭导航" onClick={()=>setMobileNav(false)}/>}
  <div className="app-main"><header className="topbar"><div className="breadcrumbs"><button className="icon-button mobile-menu" aria-label="展开导航" onClick={()=>setMobileNav(v=>!v)}><Blocks size={18}/></button><span>控制台</span><ChevronDown className="breadcrumb-chevron" size={13}/><strong>{TITLES[page]||'运行概览'}</strong></div><div className="top-actions"><button className="search-trigger" onClick={()=>{setQuery('');setPalette(true);}}><Search size={15}/><span>搜索页面或游戏</span><kbd>Ctrl K</kbd></button><span className="top-divider"/><button className="icon-button" title="使用说明" onClick={()=>{navigate('settings');}}><BookOpen size={17}/></button><button className="icon-button" aria-label={theme==='light'?'切换深色主题':'切换浅色主题'} onClick={()=>setTheme(theme==='light'?'dark':'light')}>{theme==='light'?<Moon size={17}/>:<Sun size={17}/>}</button><span className="admin-avatar">A</span></div></header>
  <main className="content">{(games.error||snapshot.error)&&<Alert tone="danger">{games.error||snapshot.error}</Alert>}<Suspense fallback={<Loading/>}>{content}</Suspense></main><footer className="app-footer"><span><span className="footer-dot"/>所有数据保存在本机 PostgreSQL</span><span>你的定义，你的决定。<span className="footer-version">MYTHWEAVER 0.9.5</span></span></footer></div>
  {toast&&<div role="status" className={'toast '+(toast.error?'danger':'success')}><span>{toast.text}</span><button aria-label="关闭提示" onClick={()=>setToast(null)}><X size={16}/></button></div>}
  {create&&<Modal title={edit?'编辑游戏信息':'创建空白游戏'} onClose={()=>setCreate(false)}><form onSubmit={e=>{e.preventDefault();void act(async()=>{const game=await api<Game>(edit?'/games/'+gameId:'/games',edit?'PATCH':'POST',{name,description,...(edit?{version:snap!.game.version}:{})});setGameId(game.id);setCreate(false);navigate('overview');},edit?'游戏信息已更新':'空白游戏已创建');}}><Alert>不附带任何玩法、人物或战斗系统。你可以随时定义自己的内容。</Alert><label>游戏名称<input autoFocus value={name} onChange={e=>setName(e.target.value)} maxLength={120} required placeholder="为这场游戏起个名字"/></label><label>说明<textarea value={description} onChange={e=>setDescription(e.target.value)} maxLength={4000} placeholder="描述想法，或暂时留空"/></label><div className="form-footer"><button type="button" className="secondary" onClick={()=>setCreate(false)}>取消</button><button className="primary" disabled={busy}>{edit?'保存修改':'创建空间'}</button></div></form></Modal>}
  {deleting&&<GameDeletion game={deleting} act={act} busy={busy} onClose={()=>setDeleting(null)} onDeleted={async()=>{setRemovedIds(ids=>[...ids,deleting.id]);setDeleting(null);setGameId(availableGames?.find(game=>game.id!==deleting.id)?.id||'');navigate('overview');}}/>}
  {palette&&<Modal title="快速前往" onClose={()=>setPalette(false)}><div className="search-input"><Search size={16}/><input autoFocus value={query} onChange={e=>setQuery(e.target.value)} placeholder="输入页面或游戏名称"/></div><div className="command-results">{NAV.flatMap(g=>g.items).filter(item=>item.label.includes(query)).map(item=><button key={item.key} onClick={()=>{navigate(item.key);setPalette(false);}}><item.icon size={17}/>{item.label}<ArrowRight size={14}/></button>)}{availableGames?.filter(g=>g.name.includes(query)).map(g=><button key={g.id} onClick={()=>{setGameId(g.id);navigate('overview');setPalette(false);}}><FolderKanban size={17}/>{g.name}<small>游戏空间</small></button>)}</div></Modal>}
  </div>;
}
