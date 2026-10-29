import {useState} from 'react';
import {Trash2} from 'lucide-react';
import {api} from '../api';
import type {Act,Game} from '../types';
import {Alert,Modal} from '../ui';

export function GameDeletion({game,act,busy,onClose,onDeleted}:{game:Game;act:Act;busy:boolean;onClose:()=>void;onDeleted:()=>Promise<void>}){
  const [confirmation,setConfirmation]=useState('');
  return <Modal title={'删除游戏：'+game.name} onClose={()=>{if(!busy)onClose();}}><form onSubmit={event=>{event.preventDefault();if(confirmation!==game.name||busy)return;void act(async()=>{await api(`/games/${game.id}`,'DELETE',{version:game.version,confirmation});await onDeleted();},'游戏已删除');}}>
    <Alert tone="danger">此操作永久删除该游戏的定义、数据、流程、聊天与执行历史、插件版本记录和检查点，并解除 QQ 会话绑定。无法通过界面撤销。</Alert>
    <p>平台模型配置、全局规则和其他游戏会保留。正在执行任务或发送消息时，系统会阻止删除。</p>
    <label>输入完整游戏名称确认：<strong>{game.name}</strong><input autoFocus value={confirmation} onChange={event=>setConfirmation(event.target.value)} autoComplete="off" maxLength={120}/></label>
    <div className="form-footer"><button type="button" className="secondary" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="danger" disabled={busy||confirmation!==game.name}><Trash2 size={15}/>{busy?'正在删除…':'永久删除游戏'}</button></div>
  </form></Modal>;
}
