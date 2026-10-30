import { ArrowRight, BookOpen, Check, ChevronRight, Database, MessageSquare, Settings2, ShieldCheck, Sparkles, Users } from 'lucide-react';
import type { GamePageProps, Status } from '../types';
import { shortTime } from '../api';
import { Badge, Empty, Panel } from '../ui';
export function Overview({ snap, navigate, status }: GamePageProps & {
    status: Status | null;
}) {
    function openSubmission(id: string) { sessionStorage.setItem('mw.submission.' + snap.game.id, id); navigate('decisions'); }
    const pending = snap.submissions.filter(item => item.status === 'pending');
    const interactions = Object.values(snap.game.definition.interactions).filter(item => item.enabled !== false);
    const checks = [
        { label: '配置管理助手', detail: '连接模型，让助手帮你落实想法', done: !!status?.agent.configured, page: 'models' },
        { label: '定义游戏玩法', detail: '添加规则、数据和玩家行动', done: interactions.length > 0, page: 'agent' },
        { label: '连接 QQ 群', detail: '绑定群组，让玩家开始参与', done: snap.channels.length > 0 && !!status?.onebot.connected, page: 'settings' },
    ];
    const ready = checks.filter(item => item.done).length;
    return <div className="overview-page">
    <div className="overview-heading"><div><span className="eyebrow">你的游戏工作台</span><h1>{snap.game.name}</h1><p>{snap.game.description || '让想象落地，从这里开始。'}</p></div><button className="secondary" onClick={() => navigate('settings')}><Settings2 size={16}/>空间设置</button></div>
    <section className="overview-hero"><div className="hero-copy"><span className="hero-kicker"><span className="hero-dot"/>{snap.policy.paused ? '新互动已暂停' : '游戏空间已就绪'}</span><h2>下一个故事，<br />由你来定义。</h2><p>说说你的想法。让管理助手与你一起完善规则、<br className="desktop-break"/>实现玩法，再交给玩家体验。</p><button className="hero-action" onClick={() => navigate('agent')}><Sparkles size={18}/>与助手一起设计<ArrowRight size={18}/></button></div><div className="hero-art" aria-hidden="true"><div className="art-orbit orbit-one"/><div className="art-orbit orbit-two"/><div className="art-core"><Sparkles size={38}/></div><span className="art-node node-one"><BookOpen size={23}/></span><span className="art-node node-two"><Users size={23}/></span><span className="art-node node-three"><Database size={23}/></span><span className="art-caption">IDEA → RULES → PLAY</span></div></section>
    <div className="overview-metrics">{[
            { label: '等待裁决', value: pending.length, note: pending.length ? '需要你来决定' : '所有事项已处理', icon: MessageSquare, page: 'decisions', attention: pending.length > 0 },
            { label: '有效数据', value: snap.entities.filter(item => !item.archived).length, note: Object.keys(snap.game.definition.types).length + ' 种数据类型', icon: Database, page: 'data' },
            { label: '可用交互', value: interactions.length, note: '当前启用的游戏交互', icon: BookOpen, page: 'definition' },
            { label: '已绑定群组', value: snap.channels.length, note: status?.onebot.connected ? 'QQ 连接在线' : 'QQ 尚未连接', icon: Users, page: 'settings' },
        ].map(item => <button className={'overview-metric ' + (item.attention ? 'needs-attention' : '')} key={item.label} onClick={() => navigate(item.page)}><div><span>{item.label}</span><item.icon size={19}/></div><strong>{item.value}<ChevronRight size={17}/></strong><small>{item.note}</small></button>)}</div>
    <div className="overview-columns"><div>
      {pending.length > 0 && <button className="attention-strip" onClick={() => navigate('decisions')}><span className="attention-symbol"><ShieldCheck size={20}/></span><span><strong>{pending.length} 条互动等待你的裁决</strong><small>查看参与内容，决定接下来发生什么。</small></span><ArrowRight size={19}/></button>}
      <Panel title="最近的互动" description="当前游戏的参与与处理记录" action={<button className="text-button" onClick={() => navigate('decisions')}>查看全部<ArrowRight size={15}/></button>}>
        {snap.submissions.length ? <div className="overview-activity">{snap.submissions.slice(0, 5).map(item => <button key={item.id} onClick={() => openSubmission(item.id)}><span className={'activity-dot ' + item.status}><MessageSquare size={17}/></span><span className="activity-content"><strong>{item.interaction === '_review' ? '请求 GM 裁决' : item.interaction === '_free' ? '自由互动' : snap.game.definition.interactions[item.interaction]?.label || item.interaction}</strong><span>{Object.values(item.data).map(value => typeof value === 'string' ? value : JSON.stringify(value)).join(' · ')}</span><small>{item.actor === 'admin' ? '管理员' : item.actor} · {shortTime(item.created_at)}</small></span><Badge state={item.status}/></button>)}</div> : <Empty title="故事还没有开始" text="先在互动工作台试提交，或在规则与试玩中验证玩法。" action={<button className="secondary" onClick={() => navigate('quality')}>去规则与试玩<ArrowRight size={15}/></button>}/>}
      </Panel>
      <button className="overview-rehearsal" onClick={() => navigate('quality')}><span className="rehearsal-symbol"><BookOpen size={23}/></span><span><strong>正式开始前，先试玩一次</strong><small>使用隔离数据核对玩家对话与执行结果。</small></span><ArrowRight size={19}/></button>
    </div><div>
      <Panel title="准备好开始了吗？" description="按需完成配置，也可以先自由探索。" action={<span className="setup-count">{ready} / 3</span>}><div className="setup-progress" aria-label={`已完成 ${ready} 项，共 3 项`}>{checks.map(item => <i key={item.label} className={item.done ? 'done' : ''}/>)}</div><div className="setup-list">{checks.map((item, index) => <button key={item.label} onClick={() => navigate(item.page)}><span className={'setup-number ' + (item.done ? 'done' : '')}>{item.done ? <Check size={16}/> : index + 1}</span><span><strong>{item.label}</strong><small>{item.detail}</small></span><ChevronRight size={16}/></button>)}</div><p className="setup-footnote">配置完成后，仍需实际试玩验收。</p></Panel>
      <Panel title="连接状态"><div className="overview-connections"><button onClick={() => navigate('models')}><span><Sparkles size={17}/>管理模型</span><span className={'status-text ' + (status?.agent.configured ? 'online' : '')}>{status?.agent.configured ? '已配置' : '待配置'}<ChevronRight size={14}/></span></button><button onClick={() => navigate('operations')}><span><Users size={17}/>QQ 消息</span><span className={'status-text ' + (status?.onebot.connected ? 'online' : '')}>{status?.onebot.connected ? '在线' : '离线'}<ChevronRight size={14}/></span></button></div><p className="setup-footnote">消息是否触发助手，由游戏与平台规则决定。</p></Panel>
    </div></div>
  </div>;
}
