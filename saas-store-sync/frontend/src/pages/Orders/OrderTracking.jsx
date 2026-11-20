import { useEffect, useState } from 'react';
import { AlertTriangle, Check, Copy, History, Loader2, MoreHorizontal, Pencil, RefreshCw, X } from 'lucide-react';
import Button from '../../components/ui/Button';
import Input from '../../components/ui/Input';

// Tracking Bot: tracking numbers read from the vendor (Amazon "Track package"), with Replace for each,
// and the status line of the three laptop bots (order bot, Tracking Bot, inventory bot).

function shortTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const sameDay = d.toDateString() === new Date().toDateString();
    const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    return sameDay ? time : `${d.toLocaleDateString([], { day: 'numeric', month: 'short' })} ${time}`;
}

function nameWarning(note) {
    const m = /name on the vendor's tracking page \(([^)]*)\) is not the customer \(([^)]*)\)/i.exec(note || '');
    return m ? `Name on Amazon's tracking page (${m[1]}) is not the customer (${m[2]})` : '';
}

function CopyChip({ value }) {
    const [copied, setCopied] = useState(false);
    const copy = () => {
        try {
            navigator.clipboard?.writeText(value);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch {
            /* clipboard not allowed: nothing to do */
        }
    };
    return (
        <button
            type="button"
            onClick={copy}
            title={copied ? 'Copied' : 'Copy tracking number'}
            className="inline-flex max-w-full items-center gap-1 rounded-md bg-slate-100 px-2 py-0.5 font-mono text-xs font-medium text-slate-800 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-100 dark:hover:bg-slate-700"
        >
            <span className="truncate">{value}</span>
            {copied ? <Check className="h-3 w-3 shrink-0 text-emerald-500" /> : <Copy className="h-3 w-3 shrink-0 opacity-50" />}
        </button>
    );
}

function IconButton({ title, onClick, children, disabled = false }) {
    return (
        <button
            type="button"
            title={title}
            aria-label={title}
            onClick={onClick}
            disabled={disabled}
            className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 disabled:opacity-40 dark:hover:bg-slate-800 dark:hover:text-slate-200"
        >
            {children}
        </button>
    );
}

function ActionsMenu({ items }) {
    // Placed against the window (fixed), so the table's scroll box never cuts it off on the last rows.
    const [pos, setPos] = useState(null);
    if (!items.length) return null;
    const toggle = (e) => {
        if (pos) {
            setPos(null);
            return;
        }
        const r = e.currentTarget.getBoundingClientRect();
        const below = window.innerHeight - r.bottom > 100;
        setPos({ left: Math.max(8, r.right - 176), top: below ? r.bottom + 4 : undefined,
            bottom: below ? undefined : window.innerHeight - r.top + 4 });
    };
    return (
        <span className="inline-flex">
            <IconButton title="Tracking actions" onClick={toggle}>
                <MoreHorizontal className="h-4 w-4" />
            </IconButton>
            {pos && (
                <>
                    <span className="fixed inset-0 z-30" onClick={() => setPos(null)} onWheel={() => setPos(null)} aria-hidden="true" />
                    <span style={pos} className="fixed z-40 w-44 overflow-hidden rounded-md border border-slate-200 bg-white py-1 text-xs shadow-lg dark:border-slate-700 dark:bg-slate-800">
                        {items.map((it) => (
                            <button
                                key={it.label}
                                type="button"
                                className="block w-full px-3 py-2 text-left text-slate-700 hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-700"
                                onClick={() => { setPos(null); it.onClick(); }}
                            >
                                {it.label}
                            </button>
                        ))}
                    </span>
                </>
            )}
        </span>
    );
}

export function TrackingCell({ order, onReplace, onGet, gettingId }) {
    const t = order.tracking;
    if (!t) return <span className="text-xs text-slate-400">—</span>;
    const current = t.current || [];
    const replaced = t.replaced || [];
    const busy = gettingId === order.id || t.pending;
    const purchased = order.purchase_check?.purchase_status === 'purchased';
    const warning = nameWarning(t.note);
    const checked = shortTime(t.checked_at);
    const menu = [
        ...(t.can_get && !busy ? [{ label: 'Get tracking now', onClick: () => onGet(order) }] : []),
        ...(purchased && current.length === 0 ? [{ label: 'Enter tracking', onClick: () => onReplace(order, null) }] : []),
    ];

    if (busy) {
        return (
            <span className="inline-flex items-center gap-1.5 text-xs text-sky-600 dark:text-sky-400" title={t.note || ''}>
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Getting tracking…
            </span>
        );
    }

    if (current.length === 0) {
        const label = {
            not_shipped: `Not shipped${checked ? ` · checked ${checked}` : ''}`,
            failed: "Couldn't read",
            cancelled: 'Cancelled at Amazon',
        }[t.status] || 'Not checked yet';
        return (
            <div className="flex items-center gap-1">
                <span
                    className={`text-xs ${t.status === 'failed' ? 'font-medium text-rose-600 dark:text-rose-400' : 'text-slate-500 dark:text-slate-400'}`}
                    title={t.note || ''}
                >
                    {label}
                </span>
                {t.status === 'failed' && t.can_get && (
                    <IconButton title="Try again" onClick={() => onGet(order)}>
                        <RefreshCw className="h-3.5 w-3.5" />
                    </IconButton>
                )}
                <ActionsMenu items={menu} />
            </div>
        );
    }

    return (
        <div className="min-w-[9rem] max-w-[15rem] space-y-1.5">
            {current.map((row) => (
                <div key={row.id}>
                    <div className="flex items-center gap-1">
                        <CopyChip value={row.tracking_number} />
                        {warning && (
                            <span title={warning} className="text-amber-500">
                                <AlertTriangle className="h-3.5 w-3.5" />
                            </span>
                        )}
                        <IconButton title="Replace tracking" onClick={() => onReplace(order, row)}>
                            <Pencil className="h-3.5 w-3.5" />
                        </IconButton>
                    </div>
                    <p className="mt-0.5 flex items-center gap-1 pl-0.5 text-[11px] text-slate-500 dark:text-slate-400">
                        <span>{row.carrier || 'Carrier not shown'}</span>
                        {row.source === 'user' && <span className="rounded bg-slate-100 px-1 text-[10px] dark:bg-slate-800">you</span>}
                        {replaced.length > 0 && row.source === 'user' && (
                            <span
                                className="inline-flex items-center"
                                title={`Replaced: ${replaced.map((r) => `${r.tracking_number}${r.carrier ? ` (${r.carrier})` : ''}`).join(', ')}`}
                            >
                                <History className="h-3 w-3" />
                            </span>
                        )}
                    </p>
                </div>
            ))}
            {t.status === 'partly' && (
                <p className="flex items-center gap-1 text-[11px] text-slate-500 dark:text-slate-400" title={t.note || ''}>
                    More packages not shipped yet
                    <ActionsMenu items={menu} />
                </p>
            )}
        </div>
    );
}

const BOTS = [['order', 'Order bot'], ['tracking', 'Tracking Bot'], ['inventory', 'Inventory bot']];

function trackingRunText(run) {
    if (!run) return '';
    if (run.status === 'pending') return 'waiting for the Tracking Bot';
    if (run.status === 'running') return `running${run.laptop ? ` on ${run.laptop}` : ''}`;
    const when = shortTime(run.finished_at);
    if (run.status === 'finished') return `${run.found} found for ${run.orders} order${run.orders === 1 ? '' : 's'}${when ? ` · ${when}` : ''}`;
    return `${run.status}${run.error ? `: ${run.error}` : ''}${when ? ` · ${when}` : ''}`;
}

export function BotStatusLine({ bots, fetchText, trackingRun }) {
    return (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
            {bots && BOTS.map(([key, label]) => {
                const b = bots[key] || {};
                const seen = shortTime(b.seen_at);
                const title = b.online
                    ? `${label} online${b.laptop ? ` (${b.laptop})` : ''}`
                    : seen ? `${label} offline · last seen ${seen}${b.laptop ? ` (${b.laptop})` : ''}` : `${label} not seen yet`;
                return (
                    <span key={key} className="inline-flex items-center gap-1.5" title={title}>
                        <span className={`inline-block h-2 w-2 rounded-full ${b.online ? 'bg-emerald-500' : 'bg-slate-400 dark:bg-slate-600'}`} />
                        <span className="text-slate-700 dark:text-slate-300">{label}</span>
                        <span>{b.online ? 'online' : 'offline'}</span>
                    </span>
                );
            })}
            {(fetchText || trackingRun) && <span className="hidden h-3 w-px bg-slate-300 sm:inline-block dark:bg-slate-700" />}
            {fetchText && <span>Last fetch: {fetchText}</span>}
            {trackingRun && <span>Last tracking: {trackingRunText(trackingRun)}</span>}
        </div>
    );
}

export function ReplaceTrackingModal({ open, order, tracking, loading, onClose, onSubmit }) {
    const [number, setNumber] = useState('');
    const [carrier, setCarrier] = useState('');
    const [error, setError] = useState('');

    useEffect(() => {
        if (!open) return;
        setNumber('');
        setCarrier(tracking?.carrier || '');
        setError('');
    }, [open, tracking]);

    if (!open) return null;

    const submit = (e) => {
        e.preventDefault();
        if (!number.trim()) {
            setError('Enter the tracking number.');
            return;
        }
        onSubmit({ tracking_number: number.trim(), carrier: carrier.trim(), tracking_id: tracking?.id || '' });
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="fixed inset-0 bg-black/50 backdrop-blur-xs" onClick={onClose} aria-hidden="true" />
            <div className="relative w-full max-w-md overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl dark:border-slate-700 dark:bg-slate-900">
                <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4 dark:border-slate-700">
                    <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
                        {tracking ? 'Replace tracking' : 'Enter tracking'} — {order?.invoice_number || order?.external_order_key}
                    </h2>
                    <button type="button" className="rounded-md p-2 text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800" onClick={onClose}>
                        <X className="h-5 w-5" />
                    </button>
                </div>
                <form onSubmit={submit} className="space-y-4 px-6 py-4">
                    {error && (
                        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-800 dark:bg-rose-900/20 dark:text-rose-300">
                            {error}
                        </div>
                    )}
                    {tracking && (
                        <p className="text-sm text-slate-600 dark:text-slate-400">
                            Now: <span className="font-mono font-medium text-slate-900 dark:text-slate-100">{tracking.tracking_number}</span>
                            {tracking.carrier ? ` (${tracking.carrier})` : ''}. The old number is kept in the order&apos;s history.
                        </p>
                    )}
                    <Input label="Tracking number" value={number} onChange={(e) => setNumber(e.target.value)} autoFocus required />
                    <Input label="Carrier" value={carrier} onChange={(e) => setCarrier(e.target.value)} placeholder="e.g. Australia Post" />
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                        The Tracking Bot does not change a number you entered.
                    </p>
                    <div className="flex justify-end gap-2 pt-2">
                        <Button variant="secondary" type="button" onClick={onClose} disabled={loading}>
                            Cancel
                        </Button>
                        <Button variant="primary" type="submit" disabled={loading}>
                            {loading ? 'Saving…' : tracking ? 'Replace' : 'Save'}
                        </Button>
                    </div>
                </form>
            </div>
        </div>
    );
}
