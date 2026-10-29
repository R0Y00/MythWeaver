import {useEffect,useRef,useState} from 'react';
import {Send,Trash2} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {api,pretty,requestId,useResource} from '../api';
import type {Act,Schema,Snapshot} from '../types';
import {Alert,Badge,Panel} from '../ui';
import './player-rehearsal.css';

function ReadableValue({value,schema}:{value:unknown;schema?:Schema}){
  if(Array.isArray(value))return <details><summary>{value.length} 项内容</summary><ul>{value.map((item,i)=><li key={i}><ReadableValue value={item} schema={schema?.items}/></li>)}</ul></details>;
  if(value!==null&&typeof value==='object')return <dl className="rehearsal-fields">{Object.entries(value).map(([key,item])=><div key={key}><dt>{schema?.properties?.[key]?.title||key}</dt><dd><ReadableValue value={item} schema={schema?.properties?.[key]}/></dd></div>)}</dl>;
  return <>{value===null?'未填写':typeof value==='boolean'?(value?'是':'否'):String(value)}</>;
}

export function PlayerRehearsal({snap,act,busy}:{snap:Snapshot;act:Act;busy:boolean}){
  const gameId=snap.game.id;
  const members=useResource<{actor:string;label:string;blocked:boolean}[]>(`/games/${gameId}/participants`);
  const [audience,setAudience]=useState('private'),[qq,setQQ]=useState(''),[prompt,setPrompt]=useState('');
  const [turns,setTurns]=useState<{prompt:string;receipt:any}[]>([]),[visible,setVisible]=useState<any>(null);
  const [conversation,setConversation]=useState(()=>requestId());
  const attempt=useRef<{scope:string;prompt:string;key:string}|null>(null),end=useRef<HTMLDivElement>(null);
  const actor='qq:'+qq,valid=/^[0-9]{1,25}$/.test(qq);
  function clear(){setTurns([]);setVisible(null);setPrompt('');setConversation(requestId());attempt.current=null;}
  useEffect(()=>{clear();setQQ('');},[gameId]);
  useEffect(()=>{end.current?.scrollIntoView({block:'nearest'});},[turns.length]);
  const enabled=snap.policy.player_agent_enabled&&!snap.policy.paused;
  return <Panel title="和玩家助手试聊" description="先选玩家，再像发 QQ 一样说话。这里执行的行动会写入当前游戏，不发送 QQ 消息。"
    action={<button className="text-button" disabled={busy||!turns.length} onClick={clear}><Trash2 size={14}/>清空对话</button>}>
    {!enabled&&<Alert tone="warning">{snap.policy.paused?'游戏已暂停，请先恢复接收。':'玩家助手尚未启用。可以让管理助手根据你的玩法完成配置。'}</Alert>}
    <div className="two-col">
      <label>以谁的身份参与<input aria-label="试聊玩家QQ号" list="rehearsal-players" inputMode="numeric" value={qq} disabled={busy} onChange={e=>{setQQ(e.target.value.replace(/^qq:/,''));clear();}} placeholder="输入玩家QQ号"/><datalist id="rehearsal-players">{members.data?.filter(m=>!m.blocked).map(m=><option key={m.actor} value={m.actor.replace(/^qq:/,'')}>{m.label||m.actor.replace(/^qq:/,'')}</option>)}</datalist><small>沿用这位玩家的现有资料；不会自动创建角色。</small></label>
      <label>模拟场景<select value={audience} disabled={busy} onChange={e=>{setAudience(e.target.value);clear();}}><option value="private">一对一私聊</option><option value="group">群内发言（只读取公开资料）</option></select><small>更换玩家、场景或清空对话只重置聊天上下文，保留已发生的游戏变化。</small></label>
    </div>
    <div className="rehearsal-chat" role="log" aria-label="玩家试聊记录" aria-live="polite">
      {!turns.length&&<div className="rehearsal-empty">在下面说一句话，看看玩家实际会收到什么。<br/><small>例如打个招呼、查询自己的情况，或尝试你已经配置的玩法。</small></div>}
      {turns.map((turn,index)=><div className="rehearsal-turn" key={index}>
        <div className="rehearsal-message player"><small>玩家</small><p>{turn.prompt}</p></div>
        <div className="rehearsal-message assistant"><div className="rehearsal-meta"><strong>玩家助手</strong><Badge state={turn.receipt.action?.status==='pending'||turn.receipt.action?.flow_status==='waiting'?'pending':undefined}>{turn.receipt.action?.status==='confirmation_required'?'等待玩家确认':turn.receipt.action?.status==='pending'||turn.receipt.action?.flow_status==='waiting'?'等待GM裁决':turn.receipt.action?.status==='applied'?'行动已处理':turn.receipt.action?'未执行本次行动':'对话回复'}</Badge></div><ReactMarkdown remarkPlugins={[remarkGfm]}>{turn.receipt.answer}</ReactMarkdown>
          <details className="rehearsal-debug"><summary>排查详情</summary><pre>{pretty(turn.receipt)}</pre></details>
        </div>
      </div>)}<div ref={end}/>
    </div>
    <form onSubmit={e=>{e.preventDefault();if(!valid||!prompt.trim()||busy)return;
      const text=prompt.trim(),scope=[gameId,actor,audience,conversation].join(':');
      if(attempt.current?.scope!==scope||attempt.current.prompt!==text)attempt.current={scope,prompt:text,key:requestId()};
      const key=attempt.current.key;
      void act(async()=>{const receipt=await api(`/games/${gameId}/player/turn`,'POST',{actor,prompt:text,key,audience,conversation_id:conversation});setTurns(t=>[...t,{prompt:text,receipt}]);setVisible(null);setPrompt('');attempt.current=null;});
    }}>
      <label className="sr-only" htmlFor="rehearsal-message">玩家想说的话</label><textarea id="rehearsal-message" value={prompt} onChange={e=>setPrompt(e.target.value)} maxLength={8000} rows={3} required disabled={busy} placeholder="像在 QQ 里一样说话…"/>
      <div className="button-row rehearsal-actions"><button className="text-button" type="button" disabled={busy||!valid||!enabled} onClick={()=>void act(async()=>setVisible(await api(`/games/${gameId}/player/records/${actor}?audience=${audience}`)))}>查看这位玩家能看到的资料</button><button className="primary" disabled={busy||!valid||!prompt.trim()||!enabled}><Send size={14}/>{busy?'正在处理…':'发送'}</button></div>
    </form>
    {visible&&<details open className="rehearsal-visible"><summary>当前可见资料 · {visible.records?.length||0} 条</summary>{!visible.records?.length&&<p>没有可直接读取的资料；已开放的玩法仍可由服务端处理隐藏状态。</p>}{visible.records?.map((record:any)=><section key={record.id}><h4>{snap.game.definition.types[record.type_key]?.label||'游戏资料'}</h4><ReadableValue value={record.data} schema={snap.game.definition.types[record.type_key]?.schema}/></section>)}</details>}
  </Panel>;
}
