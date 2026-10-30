import React, { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { ArrowRight, Blocks, Check, ChevronDown, Command, FolderKanban, LogOut, Menu, Moon, Plus, RefreshCw, Search, ShieldCheck, Sparkles, Sun, X } from 'lucide-react';
import { api, ApiError, useResource } from './api';
import type { Act, Game, Snapshot, Status } from './types';
import { Alert, Empty, Loading, Modal } from './ui';
import { navigation, destinations, mobileDestinations, validPage } from './navigation';
import { Overview } from './pages/overview';
import { GameDeletion } from './pages/game-deletion';
const DataPage = lazy(() => import('./pages/workspace').then(module => ({ default: module.DataPage })));
const DefinitionPage = lazy(() => import('./pages/workspace').then(module => ({ default: module.DefinitionPage })));
const DecisionsPage = lazy(() => import('./pages/workspace').then(module => ({ default: module.DecisionsPage })));
const SettingsPage = lazy(() => import('./pages/management').then(module => ({ default: module.SettingsPage })));
const ParticipantsPage = lazy(() => import('./pages/management').then(module => ({ default: module.ParticipantsPage })));
const BlueprintsPage = lazy(() => import('./pages/management').then(module => ({ default: module.BlueprintsPage })));
const HistoryPage = lazy(() => import('./pages/management').then(module => ({ default: module.HistoryPage })));
const OperationsPage = lazy(() => import('./pages/management').then(module => ({ default: module.OperationsPage })));
const ModelsPage = lazy(() => import('./pages/ai').then(module => ({ default: module.ModelsPage })));
const FlowsPage = lazy(() => import('./pages/flows').then(module => ({ default: module.FlowsPage })));
const ControlPage = lazy(() => import('./pages/control').then(module => ({ default: module.ControlPage })));
const PlatformRulesPage = lazy(() => import('./pages/platform').then(module => ({ default: module.PlatformRulesPage })));
const QualityPage = lazy(() => import('./pages/quality').then(module => ({ default: module.QualityPage })));
const AgentPage = lazy(() => import('./pages/assistant').then(module => ({ default: module.AgentPage })));
export default function App() {
    const [logged, setLogged] = useState<boolean | null>(null), [password, setPassword] = useState(''), [loginError, setLoginError] = useState(''), [connectionError, setConnectionError] = useState('');
    const [page, setPage] = useState(() => validPage(location.hash.slice(1) || localStorage.getItem('mw.page'))), [gameId, setGameId] = useState(localStorage.getItem('mw.game') || '');
    const [theme, setTheme] = useState(localStorage.getItem('mw.theme') || 'light'), [mobileNav, setMobileNav] = useState(false);
    const [busy, setBusy] = useState(false), [toast, setToast] = useState<{
        text: string;
        error?: boolean;
    } | null>(null);
    const [create, setCreate] = useState(false), [edit, setEdit] = useState(false), [name, setName] = useState(''), [description, setDescription] = useState('');
    const [deleting, setDeleting] = useState<Game | null>(null), [removedIds, setRemovedIds] = useState<string[]>([]);
    const [palette, setPalette] = useState(false), [query, setQuery] = useState('');
    const main = useRef<HTMLElement>(null), sidebar = useRef<HTMLElement>(null);
    const games = useResource<Game[]>(logged ? '/games' : null);
    const availableGames = games.data?.filter(game => !removedIds.includes(game.id));
    const status = useResource<Status>(logged ? '/status' : null, 5000);
    const snapshot = useResource<Snapshot>(logged && gameId ? '/games/' + gameId : null, 5000);
    const snap = snapshot.data?.game.id === gameId ? snapshot.data : null;
    const currentGame = availableGames?.find(game => game.id === gameId);
    const destination = destinations.find(item => item.key === page)!;
    const refresh = useCallback(() => { games.refresh(); status.refresh(); snapshot.refresh(); }, [games.refresh, status.refresh, snapshot.refresh]);
    const act: Act = async (task, success) => { setBusy(true); try {
        await task();
        refresh();
        if (success)
            setToast({ text: success });
        return true;
    }
    catch (e) {
        setToast({ text: (e as Error).message, error: true });
        return false;
    }
    finally {
        setBusy(false);
    } };
    function navigate(key: string) { const next = validPage(key); setPage(next); setMobileNav(false); localStorage.setItem('mw.page', next); if (location.hash !== '#' + next)
        location.hash = next; }
    function selectGame(id: string) { setGameId(id); navigate('overview'); }
    async function checkSession() { setConnectionError(''); try {
        await api('/status');
        setLogged(true);
    }
    catch (e) {
        if (e instanceof ApiError && e.status === 401)
            setLogged(false);
        else
            setConnectionError((e as Error).message);
    } }
    useEffect(() => { void checkSession(); }, []);
    useEffect(() => { localStorage.setItem('mw.game', gameId); }, [gameId]);
    useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem('mw.theme', theme); }, [theme]);
    useEffect(() => {
        if (games.loading || !games.data)
            return;
        const items = games.data.filter(game => !removedIds.includes(game.id));
        setGameId(current => items.some(game => game.id === current) ? current : items[0]?.id || '');
    }, [games.data, games.loading, removedIds]);
    useEffect(() => { if (!toast || toast.error)
        return; const timer = setTimeout(() => setToast(null), 4500); return () => clearTimeout(timer); }, [toast]);
    useEffect(() => { const handler = () => setPage(validPage(location.hash.slice(1))); window.addEventListener('hashchange', handler); return () => window.removeEventListener('hashchange', handler); }, []);
    useEffect(() => { document.title = destination.label + ' · MythWeaver'; main.current?.scrollTo({ top: 0 }); window.scrollTo({ top: 0, behavior: 'instant' }); }, [page, gameId]);
    useEffect(() => { const handler = (event: KeyboardEvent) => { if ((event.ctrlKey || event.metaKey) && event.key === 'k' && logged) {
        event.preventDefault();
        setQuery('');
        setPalette(v => !v);
    } }; document.addEventListener('keydown', handler); return () => document.removeEventListener('keydown', handler); }, [logged]);
    useEffect(() => {
        if (!mobileNav)
            return;
        const old = document.activeElement as HTMLElement;
        const overflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        sidebar.current?.querySelector<HTMLElement>('button')?.focus();
        const handler = (event: KeyboardEvent) => { if (event.key === 'Escape')
            setMobileNav(false); if (event.key === 'Tab') {
            const items = Array.from(sidebar.current?.querySelectorAll<HTMLElement>('button,select') || []).filter(item => item.offsetParent !== null && !item.hasAttribute('disabled'));
            const first = items[0], last = items.at(-1);
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last?.focus();
            }
            else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first?.focus();
            }
        } };
        document.addEventListener('keydown', handler);
        return () => { document.body.style.overflow = overflow; document.removeEventListener('keydown', handler); old?.focus(); };
    }, [mobileNav]);
    const pending = snap?.submissions.filter(s => s.status === 'pending').length || 0;
    function newGame() { setMobileNav(false); setEdit(false); setName(''); setDescription(''); setCreate(true); }
    function openSearch() { setQuery(''); setPalette(true); }
    const matches = destinations.filter(item => (item.label + item.description).toLowerCase().includes(query.trim().toLowerCase()));
    const matchingGames = availableGames?.filter(game => game.name.toLowerCase().includes(query.trim().toLowerCase())) || [];
    if (logged === null)
        return <div className="connection-screen"><div className="brand-mark"><Blocks /></div>{connectionError ? <><h1>暂时无法连接工作台</h1><p>{connectionError}</p><button className="primary" onClick={() => void checkSession()}><RefreshCw size={16}/>重新连接</button></> : <Loading />}</div>;
    if (!logged)
        return <div className="login-screen"><section className="login-story"><div className="login-brand"><span className="brand-mark"><Blocks size={23}/></span>MythWeaver</div><div className="story-main"><span className="overline">为每一个世界，留出可能。</span><h1>你的想象，<br />开始有了世界。</h1><p>从一个想法到一场共同参与的游戏。<br />在这里定义规则、连接玩家，与 AI 一起创造。</p><div className="login-orbit" aria-hidden="true"><div /><div /><span><Sparkles size={32}/></span></div></div><small>你的定义，你的决定。</small></section><section className="login-form-wrap"><form className="login-form" onSubmit={event => { event.preventDefault(); setLoginError(''); setBusy(true); api('/login', 'POST', { password }).then(() => { setLogged(true); setPassword(''); }).catch(e => setLoginError(e.message)).finally(() => setBusy(false)); }}><span className="eyebrow">MYTHWEAVER WORKSPACE</span><h2>欢迎回到工作台</h2><p>登录后，继续构建你的游戏世界。</p><label htmlFor="admin-password">管理员密码</label><input id="admin-password" type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required placeholder="输入管理员密码"/>{loginError && <Alert tone="danger">{loginError}</Alert>}<button className="primary full login-submit" disabled={busy}>{busy ? '正在登录…' : '进入工作台'}<ArrowRight size={17}/></button><div className="login-hint"><ShieldCheck size={16}/><span>首次登录？密码保存在部署电脑的<br /><code>.runtime/credentials.txt</code></span></div></form></section></div>;
    const props = snap ? { snap, act, busy, navigate } : null;
    let content: React.ReactNode;
    if (page === 'models')
        content = <ModelsPage act={act} busy={busy}/>;
    else if (page === 'blueprints')
        content = <BlueprintsPage key={gameId} snap={snap} act={act} busy={busy} onCreated={id => { selectGame(id); refresh(); }}/>;
    else if (page === 'platform')
        content = <PlatformRulesPage act={act} busy={busy}/>;
    else if (page === 'operations')
        content = <OperationsPage key={gameId} snap={snap} status={status.data} act={act} busy={busy}/>;
    else if (games.loading && !games.data)
        content = <Loading />;
    else if (games.error && !games.data)
        content = <Empty title="游戏列表加载失败" text={games.error} action={<button onClick={games.refresh}>重新加载</button>}/>;
    else if (!gameId || !availableGames?.length)
        content = <div className="welcome-empty"><span className="brand-mark"><Blocks size={30}/></span><span className="eyebrow">从一个想法开始</span><h1>创建你的第一个游戏空间</h1><p>可以只从文字互动开始。<br />规则、数据和玩家行动，都可以与助手一起逐步完善。</p><button className="primary" onClick={newGame}><Plus size={17}/>创建游戏空间</button><div className="welcome-steps"><span>01 描述想法</span><span>02 定义玩法</span><span>03 邀请参与</span></div></div>;
    else if (!props)
        content = snapshot.error ? <Empty title="空间暂时无法加载" text={snapshot.error} action={<button onClick={snapshot.refresh}>重试</button>}/> : <Loading />;
    else if (page === 'definition')
        content = <DefinitionPage key={gameId} {...props}/>;
    else if (page === 'control')
        content = <ControlPage key={gameId} {...props}/>;
    else if (page === 'quality')
        content = <QualityPage key={gameId} {...props}/>;
    else if (page === 'flows')
        content = <FlowsPage key={gameId} {...props}/>;
    else if (page === 'data')
        content = <DataPage key={gameId} {...props}/>;
    else if (page === 'decisions')
        content = <DecisionsPage key={gameId} {...props}/>;
    else if (page === 'settings')
        content = <SettingsPage key={gameId} {...props} onDelete={() => setDeleting(snap!.game)} onEditInfo={() => { setEdit(true); setName(snap!.game.name); setDescription(snap!.game.description); setCreate(true); }}/>;
    else if (page === 'participants')
        content = <ParticipantsPage key={gameId} {...props}/>;
    else if (page === 'agent')
        content = <AgentPage key={gameId} {...props} status={status.data}/>;
    else if (page === 'history')
        content = <HistoryPage key={gameId} {...props}/>;
    else
        content = <Overview key={gameId} {...props} status={status.data}/>;
    return <div className={'app-shell ' + (page === 'agent' ? 'is-assistant' : '')}>
    <a className="skip-link" href="#main-content" onClick={event => { event.preventDefault(); main.current?.focus(); }}>跳到主要内容</a>
    {mobileNav && <button className="nav-overlay" aria-label="关闭导航" onClick={() => setMobileNav(false)}/>}
    <aside ref={sidebar} id="main-navigation" className={'sidebar ' + (mobileNav ? 'open' : '')} aria-label="工作台导航">
      <div className="sidebar-brand"><button className="brand" onClick={() => navigate('overview')}><span className="brand-mark"><Blocks size={23}/></span><span>MythWeaver<small>游戏创作工作台</small></span></button><button className="icon-button sidebar-close" aria-label="关闭导航菜单" onClick={() => setMobileNav(false)}><X size={20}/></button></div>
      <div className="workspace-switch"><label htmlFor="workspace-select">当前空间</label><div className="workspace-select"><FolderKanban size={17}/><select id="workspace-select" value={gameId} onChange={e => selectGame(e.target.value)}><option value="">选择游戏空间</option>{availableGames?.map(game => <option key={game.id} value={game.id}>{game.name}</option>)}</select><ChevronDown size={14}/></div><button className="new-workspace" onClick={newGame}><Plus size={15}/>新建空间</button></div>
      <div className="nav-groups">{navigation.map(group => <nav key={group.label} aria-label={group.label}><div className="nav-label">{group.label}</div>{group.items.map(item => <button key={item.key} aria-current={page === item.key ? 'page' : undefined} className={'nav-item ' + (page === item.key ? 'active' : '')} onClick={() => navigate(item.key)}><item.icon size={18}/><span>{item.label}</span>{item.key === 'decisions' && pending > 0 && <b>{pending}</b>}</button>)}</nav>)}</div>
      <div className="sidebar-foot"><button className="connection-pill" onClick={() => navigate('operations')}><i className={status.data?.onebot.connected ? 'online' : ''}/><span>{status.error ? '连接状态获取失败' : status.data?.onebot.connected ? 'QQ 已连接' : 'QQ 未连接'}</span><ArrowRight size={14}/></button><div className="sidebar-account"><span className="avatar">GM</span><span>管理员<small>{status.data?.version ? 'v' + status.data.version : 'MythWeaver'}</small></span><button className="icon-button" aria-label="退出登录" onClick={() => void act(async () => { await api('/logout', 'POST'); setMobileNav(false); setLogged(false); })}><LogOut size={17}/></button></div></div>
    </aside>
    <div className="app-main"><header className="topbar"><div className="breadcrumbs"><button className="icon-button mobile-menu" aria-label="打开导航" aria-controls="main-navigation" aria-expanded={mobileNav} onClick={() => setMobileNav(true)}><Menu size={21}/></button><span className="breadcrumb-space">{currentGame?.name || 'MythWeaver'}</span><span className="breadcrumb-slash">/</span><strong>{destination.label}</strong></div><div className="top-actions"><button className="search-trigger" onClick={openSearch} aria-label="搜索页面或游戏"><Search size={16}/><span>搜索工作台</span><kbd>Ctrl K</kbd></button><button className="icon-button" aria-label={theme === 'light' ? '切换深色主题' : '切换浅色主题'} onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}>{theme === 'light' ? <Moon size={19}/> : <Sun size={19}/>}</button><span className="admin-avatar">GM</span></div></header>
      <main id="main-content" ref={main} tabIndex={-1} className={'content ' + (page === 'agent' ? 'content-assistant' : '')}>
        {(games.error && games.data || snapshot.error && snap) && <Alert tone="warning">数据刷新失败，当前显示上次读取的内容。<button className="text-button" onClick={refresh}>重试</button></Alert>}
        <Suspense fallback={<Loading />}>{content}</Suspense>
      </main>
    </div>
    <nav className="mobile-bottom-nav" aria-label="常用功能">{mobileDestinations.map(item => <button key={item.key} aria-current={page === item.key ? 'page' : undefined} className={page === item.key ? 'active' : ''} onClick={() => navigate(item.key)}><span><item.icon size={21}/>{item.key === 'decisions' && pending > 0 && <i>{pending}</i>}</span>{item.key === 'overview' ? '概览' : item.key === 'agent' ? '助手' : item.key === 'decisions' ? '互动' : '试玩'}</button>)}<button className={!mobileDestinations.some(item => item.key === page) ? 'active' : ''} aria-expanded={mobileNav} onClick={() => setMobileNav(true)}><Menu size={21}/>更多</button></nav>
    {toast && <div role={toast.error ? 'alert' : 'status'} className={'toast ' + (toast.error ? 'danger' : 'success')}>{toast.error ? <ShieldCheck size={18}/> : <Check size={18}/>}<span>{toast.text}</span><button aria-label="关闭提示" onClick={() => setToast(null)}><X size={17}/></button></div>}
    {create && <Modal title={edit ? '编辑游戏信息' : '创建游戏空间'} onClose={() => setCreate(false)}><form onSubmit={event => { event.preventDefault(); void act(async () => { const game = await api<Game>(edit ? '/games/' + gameId : '/games', edit ? 'PATCH' : 'POST', { name: name.trim(), description, ...(edit ? { version: snap!.game.version } : {}) }); selectGame(game.id); setCreate(false); }, edit ? '游戏信息已更新' : '空间已创建，可以开始设计玩法了'); }}><p className="muted">给你的世界起个名字。其他内容可以在创建后与管理助手一起完善。</p><label>游戏名称<input autoFocus value={name} onChange={e => setName(e.target.value)} maxLength={120} required placeholder="例如：雾港来信"/></label><label>想法或简介 <small className="inline-optional">选填</small><textarea value={description} onChange={e => setDescription(e.target.value)} maxLength={4000} placeholder="这是一个什么样的世界？玩家会一起做什么？"/></label><div className="form-footer"><button type="button" className="secondary" onClick={() => setCreate(false)}>取消</button><button className="primary" disabled={busy || !name.trim()}>{busy ? '正在保存…' : edit ? '保存修改' : '创建空间'}<ArrowRight size={16}/></button></div></form></Modal>}
    {deleting && <GameDeletion game={deleting} act={act} busy={busy} onClose={() => setDeleting(null)} onDeleted={async () => { setRemovedIds(ids => [...ids, deleting.id]); setDeleting(null); selectGame(availableGames?.find(game => game.id !== deleting.id)?.id || ''); }}/>}
    {palette && <Modal title="快速前往" onClose={() => setPalette(false)}><div className="search-input command-search"><Search size={19}/><input autoFocus aria-label="搜索页面和游戏" value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索功能、页面或游戏名称…" onKeyDown={e => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
        if (matches[0]) {
            navigate(matches[0].key);
            setPalette(false);
        }
        else if (matchingGames[0]) {
            selectGame(matchingGames[0].id);
            setPalette(false);
        }
    } }}/></div><div className="command-results">{!!matches.length && <span className="command-group">功能与页面</span>}{matches.map(item => <button key={item.key} onClick={() => { navigate(item.key); setPalette(false); }}><item.icon size={19}/><span><strong>{item.label}</strong><small>{item.description}</small></span><ArrowRight size={15}/></button>)}{!!matchingGames.length && <span className="command-group">游戏空间</span>}{matchingGames.map(game => <button key={game.id} onClick={() => { selectGame(game.id); setPalette(false); }}><FolderKanban size={19}/><span><strong>{game.name}</strong><small>切换到这个游戏</small></span><ArrowRight size={15}/></button>)}{!matches.length && !matchingGames.length && <Empty title="没有找到结果" text="试试“模型”“试玩”或游戏名称。"/>}</div><div className="command-hint"><Command size={13}/> Enter 打开首项 · Esc 关闭</div></Modal>}
  </div>;
}
