import { useEffect, useState } from 'react';
import { AlertTriangle, History, Pencil, Plus, RefreshCw, X } from 'lucide-react';
import Button from '../../components/ui/Button';
import Input from '../../components/ui/Input';

// Tracking Bot: tracking numbers read from the vendor (Amazon "Track package"), with Replace for each.

const STATUS_TEXT = {
    queued: 'Waiting for the Tracking Bot…',
    not_shipped: 'Not shipped yet',
    partly: 'More packages not shipped yet',
    failed: 'Tracking not read',
    cancelled: 'Cancelled at the vendor',
};

function noteWarns(note) {
    return /is not the customer/i.test(note || '');
}

export function TrackingCell({ order, onReplace, onGet, gettingId }) {
    const t = order.tracking;
    if (!t) return <span className="text-xs text-slate-400">—</span>;
    const current = t.current || [];
    const replaced = t.replaced || [];
    const busy = gettingId === order.id || t.pending;
    const statusText = t.pending ? STATUS_TEXT.queued : (current.length && t.status !== 'partly' ? '' : STATUS_TEXT[t.status] || '');
    const canAdd = order.purchase_check?.purchase_status === 'purchased';
    return (
        <div className="min-w-[10rem] max-w-[16rem] space-y-1">
            {current.map((row) => (
                <div key={row.id} className="flex items-start gap-1.5">
                    <div className="min-w-0">
                        <p className="break-all font-mono text-xs font-medium text-slate-900 dark:text-slate-100">{row.tracking_number}</p>
                        <p className="text-[11px] text-slate-500 dark:text-slate-400">
                            {row.carrier || 'Carrier not shown'}
                            {row.source === 'user' && <span className="ml-1 rounded bg-slate-100 px-1 text-[10px] text-slate-600 dark:bg-slate-800 dark:text-slate-300">entered by you</span>}
                        </p>
                    </div>
                    <button
                        type="button"
                        title="Replace this tracking number"
                        className="ml-auto inline-flex shrink-0 items-center gap-0.5 rounded-md px-1.5 py-0.5 text-[11px] font-medium text-accent-700 hover:bg-accent-50 dark:text-accent-300 dark:hover:bg-accent-900/20"
                        onClick={() => onReplace(order, row)}
                    >
                        <Pencil className="h-3 w-3" />
                        Replace
                    </button>
                </div>
            ))}
            {statusText && (
                <p className={`text-[11px] ${t.status === 'failed' ? 'text-rose-600 dark:text-rose-400' : 'text-slate-500 dark:text-slate-400'}`} title={t.note || ''}>
                    {statusText}
                </p>
            )}
            {noteWarns(t.note) && (
                <p className="flex items-start gap-1 text-[11px] text-amber-700 dark:text-amber-300" title={t.note}>
                    <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                    Name on the tracking page differs from the customer
                </p>
            )}
            {replaced.length > 0 && (
                <p
                    className="flex items-center gap-1 text-[11px] text-slate-400"
                    title={`Replaced: ${replaced.map((r) => `${r.tracking_number}${r.carrier ? ` (${r.carrier})` : ''}`).join(', ')}`}
                >
                    <History className="h-3 w-3" />
                    {replaced.length} replaced: <span className="font-mono line-through">{replaced[replaced.length - 1].tracking_number}</span>
                </p>
            )}
            <div className="flex flex-wrap gap-x-2">
                {t.can_get && (
                    <button
                        type="button"
                        className="inline-flex items-center gap-0.5 text-[11px] font-medium text-slate-600 hover:underline disabled:opacity-50 dark:text-slate-300"
                        onClick={() => onGet(order)}
                        disabled={busy}
                        title="The Tracking Bot opens this order on the vendor's site and reads the tracking"
                    >
                        <RefreshCw className={`h-3 w-3 ${busy ? 'animate-spin' : ''}`} />
                        {busy ? 'Getting…' : 'Get tracking'}
                    </button>
                )}
                {canAdd && current.length === 0 && (
                    <button
                        type="button"
                        className="inline-flex items-center gap-0.5 text-[11px] font-medium text-slate-600 hover:underline dark:text-slate-300"
                        onClick={() => onReplace(order, null)}
                    >
                        <Plus className="h-3 w-3" />
                        Add
                    </button>
                )}
            </div>
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
                        {tracking ? 'Replace tracking' : 'Add tracking'} — {order?.invoice_number || order?.external_order_key}
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
                    <Input label="New tracking number" value={number} onChange={(e) => setNumber(e.target.value)} autoFocus required />
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
