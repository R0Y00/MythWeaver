import React, { useEffect, useRef } from 'react';
import { AlertCircle, Check, ChevronRight, Inbox, LoaderCircle, X } from 'lucide-react';
import { stateLabels } from './api';
export function Modal({ title, onClose, children, wide = false }: {
    title: string;
    onClose: () => void;
    children: React.ReactNode;
    wide?: boolean;
}) {
    const ref = useRef<HTMLElement>(null), close = useRef(onClose);
    close.current = onClose;
    useEffect(() => {
        const old = document.activeElement as HTMLElement, overflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        (ref.current?.querySelector<HTMLElement>('[autofocus],input,textarea,select') || ref.current)?.focus();
        const handler = (event: KeyboardEvent) => {
            // Only the topmost dialog owns keyboard navigation.
            const dialogs = document.querySelectorAll('[role="dialog"]');
            if (dialogs[dialogs.length - 1] !== ref.current)
                return;
            if (event.key === 'Escape') {
                event.stopPropagation();
                close.current();
            }
            if (event.key === 'Tab') {
                const items = Array.from(ref.current?.querySelectorAll<HTMLElement>('button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled]),a[href],[tabindex="0"]') || []).filter(item => item.offsetParent !== null);
                if (!items.length) {
                    event.preventDefault();
                    return;
                }
                const first = items[0], last = items.at(-1);
                if (event.shiftKey && (document.activeElement === first || document.activeElement === ref.current)) {
                    event.preventDefault();
                    last?.focus();
                }
                else if (!event.shiftKey && (document.activeElement === last || document.activeElement === ref.current)) {
                    event.preventDefault();
                    first.focus();
                }
            }
        };
        document.addEventListener('keydown', handler);
        return () => { document.removeEventListener('keydown', handler); document.body.style.overflow = overflow; old?.focus(); };
    }, []);
    return <div className="modal-backdrop" onMouseDown={e => { if (e.target === e.currentTarget)
        onClose(); }}><section ref={ref} tabIndex={-1} className={'modal ' + (wide ? 'wide' : '')} role="dialog" aria-modal="true" aria-label={title}><div className="modal-heading"><div><span className="eyebrow">MYTHWEAVER</span><h2>{title}</h2></div><button className="icon-button" onClick={onClose} aria-label="关闭"><X size={18}/></button></div>{children}</section></div>;
}
export function Empty({ title = '这里还没有内容', text, action }: {
    title?: string;
    text: string;
    action?: React.ReactNode;
}) { return <div className="empty"><div className="empty-icon"><Inbox size={25}/></div><h3>{title}</h3><p>{text}</p>{action}</div>; }
export function Badge({ state, children }: {
    state?: string;
    children?: React.ReactNode;
}) { return <span className={'badge ' + (state || '')}>{state && <i />}{children || stateLabels[state || ''] || state}</span>; }
export function Panel({ title, description, action, children, className = '' }: {
    title?: string;
    description?: string;
    action?: React.ReactNode;
    children: React.ReactNode;
    className?: string;
}) { return <section className={'panel ' + className}>{(title || action) && <div className="panel-head"><div>{title && <h3>{title}</h3>}{description && <p>{description}</p>}</div>{action}</div>}{children}</section>; }
export function Loading() { return <div className="loading" role="status"><LoaderCircle size={20} className="spin"/> 正在载入工作空间</div>; }
export function Alert({ children, tone = 'info' }: {
    children: React.ReactNode;
    tone?: string;
}) { return <div role={tone === 'danger' ? 'alert' : 'status'} className={'alert ' + tone}><AlertCircle size={16}/><div>{children}</div></div>; }
export function Toggle({ checked, onChange, label, description }: {
    checked: boolean;
    onChange: (v: boolean) => void;
    label: string;
    description?: string;
}) { return <label className="toggle-row"><div><strong>{label}</strong>{description && <p>{description}</p>}</div><button type="button" role="switch" aria-checked={checked} aria-label={label} className={'toggle ' + (checked ? 'checked' : '')} onClick={() => onChange(!checked)}><span>{checked && <Check size={10}/>}</span></button></label>; }
export function Tabs({ items, value, onChange }: {
    items: {
        key: string;
        label: string;
        count?: number;
    }[];
    value: string;
    onChange: (key: string) => void;
}) { return <div className="tabs" role="tablist" aria-label="页面分类" onKeyDown={event => { if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key))
    return; event.preventDefault(); const index = items.findIndex(item => item.key === value); const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + items.length) % items.length; onChange(items[next].key); event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus(); }}>{items.map(item => <button key={item.key} role="tab" tabIndex={item.key === value ? 0 : -1} aria-selected={item.key === value} className={item.key === value ? 'selected' : ''} onClick={() => onChange(item.key)}>{item.label}{item.count !== undefined && <small>{item.count}</small>}</button>)}</div>; }
export function PageTitle({ title, description, action }: {
    title: string;
    description: string;
    action?: React.ReactNode;
}) { return <div className="page-title"><div><h1>{title}</h1><p>{description}</p></div>{action}</div>; }
export function NextLink({ label, description, onClick }: {
    label: string;
    description: string;
    onClick: () => void;
}) { return <button className="next-link" onClick={onClick}><div><strong>{label}</strong><span>{description}</span></div><ChevronRight size={17}/></button>; }
