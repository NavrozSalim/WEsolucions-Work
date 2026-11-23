import { useEffect, useState } from 'react';
import { AlertTriangle, Check, CheckCircle2, Clock, Copy, History, Loader2, Pencil, RefreshCw, UploadCloud, X } from 'lucide-react';
import Button from '../../components/ui/Button';
import Input from '../../components/ui/Input';
import { getTrackingCouriers, getUploadTrackingPlan, saveTrackingCouriers, startUploadTracking } from '../../services/listingService';

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

function SmallButton({ title, onClick, children, disabled = false }) {
    return (
        <button
            type="button"
            title={title}
            onClick={onClick}
            disabled={disabled}
            className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-1.5 py-0.5 text-[11px] font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
        >
            {children}
        </button>
    );
}

const UPLOAD_DONE = ['verified', 'already'];
const UPLOAD_PROBLEM = ['failed', 'not_verified'];

/** "Uploaded on marketplace" tickbox: the user pasted the number on the marketplace order themselves. */
function UploadedTick({ row, onMarkUploaded, marking }) {
    const manual = row.upload_status === 'manual';
    if (!onMarkUploaded || !(row.can_mark_uploaded || manual)) return null;
    return (
        <label
            className="inline-flex cursor-pointer items-center gap-1 text-[11px] text-slate-600 dark:text-slate-300"
            title={manual ? (row.upload_note || 'Uploaded on the marketplace') : 'Tick after you paste this number on the marketplace order yourself'}
        >
            <input
                type="checkbox"
                className="h-3.5 w-3.5 rounded border-slate-300 accent-emerald-600"
                checked={manual}
                disabled={marking}
                onChange={(e) => onMarkUploaded(e.target.checked)}
            />
            {marking ? 'Saving…' : 'Uploaded on marketplace'}
        </label>
    );
}

/** Upload to the marketplace: what the Tracking Bot did with this number, the Upload button (Kogan / Big W),
 *  and the "Uploaded on marketplace" tickbox (every marketplace). */
export function UploadStatus({ row, uploadTo, onUpload, uploading, onMarkUploaded, marking }) {
    const note = row.upload_note || '';
    const tick = <UploadedTick row={row} onMarkUploaded={onMarkUploaded} marking={marking} />;
    if (row.upload_status === 'manual') {
        return (
            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 pl-0.5">
                <span className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-600 dark:text-emerald-400" title={note}>
                    <CheckCircle2 className="h-3 w-3" />
                    On {uploadTo || 'marketplace'}{/by you|marked uploaded/i.test(note) ? ' · by you' : ''}
                </span>
                {tick}
            </div>
        );
    }
    const botCan = uploadTo && !row.vendor_own_number;
    if (uploadTo && row.vendor_own_number) {
        return (
            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 pl-0.5 text-[11px]">
                <span className="text-slate-500 dark:text-slate-400" title="Amazon's own TBA numbers can only be tracked on Amazon, so they are not uploaded to the marketplace">
                    TBA – not uploaded
                </span>
                {tick}
            </div>
        );
    }
    if (botCan && (uploading || row.upload_pending)) {
        return (
            <p className="flex items-center gap-1 pl-0.5 text-[11px] text-sky-600 dark:text-sky-400" title={note}>
                <Loader2 className="h-3 w-3 animate-spin" />
                Uploading to {uploadTo}…
            </p>
        );
    }
    if (botCan && UPLOAD_DONE.includes(row.upload_status)) {
        return (
            <p className="flex items-center gap-1 pl-0.5 text-[11px] font-medium text-emerald-600 dark:text-emerald-400" title={note}>
                <CheckCircle2 className="h-3 w-3" />
                On {uploadTo}
            </p>
        );
    }
    const problem = botCan && UPLOAD_PROBLEM.includes(row.upload_status);
    const label = !botCan ? '' : problem ? 'Upload failed' : row.upload_status === 'skipped' ? 'Not uploaded'
        : row.upload_status === 'dry_run' ? 'Dry run only' : '';
    return (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 pl-0.5 text-[11px]">
            {label && (
                <span
                    title={note}
                    className={problem ? 'font-medium text-rose-600 dark:text-rose-400' : 'text-amber-600 dark:text-amber-400'}
                >
                    {label}
                </span>
            )}
            {botCan && row.can_upload && onUpload && (
                <button
                    type="button"
                    onClick={onUpload}
                    title={`The Tracking Bot uploads this number to the ${uploadTo} order and checks it there`}
                    className="inline-flex items-center gap-1 rounded px-1 py-0.5 font-medium text-accent-600 hover:bg-slate-100 dark:text-accent-400 dark:hover:bg-slate-800"
                >
                    <UploadCloud className="h-3 w-3" />
                    {problem ? 'Try again' : `Upload to ${uploadTo}`}
                </button>
            )}
            {tick}
        </div>
    );
}

const CELL_STATUS = {
    not_shipped: { label: 'Not shipped', cls: 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300' },
    failed: { label: "Couldn't read", cls: 'bg-rose-50 text-rose-700 dark:bg-rose-900/30 dark:text-rose-300' },
    cancelled: { label: 'Cancelled at Amazon', cls: 'bg-rose-50 text-rose-700 dark:bg-rose-900/30 dark:text-rose-300' },
    partly: { label: 'More packages to ship', cls: 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300' },
};
const CELL_NOT_CHECKED = { label: 'Not checked yet', cls: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300' };

/** Get tracking / Enter tracking, always visible under the state (no hidden menu). */
function TrackingActions({ order, t, purchased, hasNumber, onGet, onReplace }) {
    const canGet = t.can_get && onGet;
    const canEnter = purchased && !hasNumber && onReplace;
    if (!canGet && !canEnter) return null;
    return (
        <div className="flex flex-wrap items-center gap-1">
            {canGet && (
                <SmallButton title="The Tracking Bot reads the tracking from Amazon now" onClick={() => onGet(order)}>
                    <RefreshCw className="h-3 w-3" /> Get tracking
                </SmallButton>
            )}
            {canEnter && (
                <SmallButton title="Type the tracking number yourself" onClick={() => onReplace(order, null)}>
                    <Pencil className="h-3 w-3" /> Enter tracking
                </SmallButton>
            )}
        </div>
    );
}

export function TrackingCell({ order, onReplace, onGet, gettingId, onUpload, uploadingId, onMarkUploaded, markingUploadedId, trackingOnline = null }) {
    const t = order.tracking;
    if (!t) return <span className="text-xs text-slate-400">—</span>;
    const current = t.current || [];
    const replaced = t.replaced || [];
    const busy = gettingId === order.id || t.pending;
    const purchased = order.purchase_check?.purchase_status === 'purchased';
    const warning = nameWarning(t.note);
    const checked = shortTime(t.checked_at);

    if (busy) {
        if (t.run_state === 'waiting' && gettingId !== order.id) {
            const offline = trackingOnline === false;
            return (
                <span
                    className="inline-flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400"
                    title={offline ? 'The Tracking Bot agent is not running: it starts this when it is.' : (t.note || 'Queued; the Tracking Bot starts it shortly')}
                >
                    <Clock className="h-3.5 w-3.5" />
                    Waiting for the Tracking Bot{offline ? ' · bot offline' : ''}
                </span>
            );
        }
        return (
            <span className="inline-flex items-center gap-1.5 text-xs text-sky-600 dark:text-sky-400" title={t.note || ''}>
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Getting tracking…
            </span>
        );
    }

    if (current.length === 0) {
        if (!purchased && !order.is_history) {
            return <span className="text-xs text-slate-400 dark:text-slate-500" title="Tracking comes after the order is bought">Not bought yet</span>;
        }
        const state = CELL_STATUS[t.status] || CELL_NOT_CHECKED;
        const tip = [t.note, checked ? `Checked ${checked}` : ''].filter(Boolean).join(' · ');
        return (
            <div className="flex flex-col items-start gap-1">
                <span className={`inline-block rounded-full px-2 py-0.5 text-[11px] font-medium ${state.cls}`} title={tip}>
                    {state.label}
                </span>
                <TrackingActions order={order} t={t} purchased={purchased} hasNumber={false} onGet={onGet} onReplace={onReplace} />
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
                        {onReplace && (
                            <IconButton title="Replace tracking" onClick={() => onReplace(order, row)}>
                                <Pencil className="h-3.5 w-3.5" />
                            </IconButton>
                        )}
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
                    <UploadStatus
                        row={row}
                        uploadTo={t.upload_to}
                        uploading={uploadingId === row.id}
                        onUpload={onUpload ? () => onUpload(order, row) : null}
                        onMarkUploaded={onMarkUploaded ? (v) => onMarkUploaded(order, row, v) : null}
                        marking={markingUploadedId === row.id}
                    />
                </div>
            ))}
            {t.status === 'partly' && (
                <div className="flex flex-col items-start gap-1">
                    <span className={`inline-block rounded-full px-2 py-0.5 text-[11px] font-medium ${CELL_STATUS.partly.cls}`} title={t.note || ''}>
                        {CELL_STATUS.partly.label}
                    </span>
                    <TrackingActions order={order} t={t} purchased={purchased} hasNumber onGet={onGet} onReplace={onReplace} />
                </div>
            )}
        </div>
    );
}

const DETAIL_STATUS = {
    queued: 'Waiting for the Tracking Bot',
    not_shipped: 'Not shipped yet',
    partly: 'Some packages not shipped yet',
    found: 'Tracking found',
    cancelled: 'Cancelled at Amazon',
    failed: "Couldn't read the tracking",
};

/** Order details (Shipping / tracking card): the vendor's tracking numbers, where each came from, history. */
export function TrackingDetails({ tracking, formatDate, onReplace, onUpload, uploadingId, onMarkUploaded, markingId, canEnter = false }) {
    const t = tracking;
    if (!t) return null;
    const current = t.current || [];
    const replaced = t.replaced || [];
    const warning = nameWarning(t.note);
    const status = t.pending ? DETAIL_STATUS.queued : DETAIL_STATUS[t.status] || 'Not checked yet';
    const note = (t.note || '').replace(/^\d{2}\/\d{2}\/\d{4} \d{2}:\d{2}: /, '');
    return (
        <div className="mb-3">
            <p className="mb-1.5 text-xs font-medium text-slate-500 dark:text-slate-400">Vendor tracking (Tracking Bot)</p>
            {current.length > 0 ? (
                <ul className="space-y-1.5">
                    {current.map((row) => (
                        <li key={row.id} className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md bg-slate-50 px-2.5 py-1.5 dark:bg-slate-800/60">
                            <CopyChip value={row.tracking_number} />
                            <span className="text-slate-700 dark:text-slate-300">{row.carrier || 'Carrier not shown'}</span>
                            <span className="text-xs text-slate-500 dark:text-slate-400">
                                {row.source === 'user' ? 'entered by you' : 'read by the Tracking Bot'}
                                {row.vendor_order_number ? ` · order ${row.vendor_order_number}` : ''}
                                {row.created_at ? ` · ${formatDate(row.created_at)}` : ''}
                            </span>
                            <UploadStatus
                                row={row}
                                uploadTo={t.upload_to}
                                uploading={uploadingId === row.id}
                                onUpload={onUpload ? () => onUpload(row) : null}
                                onMarkUploaded={onMarkUploaded ? (v) => onMarkUploaded(row, v) : null}
                                marking={markingId === row.id}
                            />
                            {onReplace && (
                                <button
                                    type="button"
                                    onClick={() => onReplace(row)}
                                    className="ml-auto inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium text-accent-600 hover:bg-slate-100 dark:text-accent-400 dark:hover:bg-slate-800"
                                >
                                    <Pencil className="h-3 w-3" />
                                    Replace
                                </button>
                            )}
                        </li>
                    ))}
                </ul>
            ) : (
                <p className="flex flex-wrap items-center gap-2 text-slate-500 dark:text-slate-400">
                    No vendor tracking yet.
                    {canEnter && onReplace && (
                        <SmallButton title="Type the tracking number yourself" onClick={() => onReplace(null)}>
                            <Pencil className="h-3 w-3" /> Enter tracking
                        </SmallButton>
                    )}
                </p>
            )}
            {replaced.length > 0 && (
                <ul className="mt-1.5 space-y-0.5 text-xs text-slate-500 dark:text-slate-400">
                    {replaced.map((row) => (
                        <li key={row.id} className="flex items-center gap-1">
                            <History className="h-3 w-3" />
                            <span className="font-mono line-through">{row.tracking_number}</span>
                            {row.carrier ? ` (${row.carrier})` : ''}
                            {row.replaced_at ? ` · replaced ${formatDate(row.replaced_at)}` : ' · replaced'}
                        </li>
                    ))}
                </ul>
            )}
            {warning && (
                <p className="mt-1.5 flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400">
                    <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                    {warning}
                </p>
            )}
            <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">
                {status}
                {t.checked_at ? ` · last checked ${formatDate(t.checked_at)}` : ''}
                {note && !warning && t.status !== 'found' ? ` · ${note}` : ''}
            </p>
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


/** Couriers for Upload tracking: what to pick on Kogan / Big W for each carrier Amazon shows. */
export function CouriersModal({ open, storeId, onClose, onSaved }) {
    const [data, setData] = useState(null);
    const [values, setValues] = useState({});
    const [error, setError] = useState('');
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        if (!open || !storeId) return;
        setData(null);
        setError('');
        getTrackingCouriers(storeId)
            .then((res) => {
                setData(res.data);
                setValues(Object.fromEntries((res.data?.rows || []).map((r) => [r.carrier, r.courier || ''])));
            })
            .catch((err) => setError(err.response?.data?.detail || 'Could not load the couriers.'));
    }, [open, storeId]);

    if (!open) return null;
    const rows = data?.rows || [];
    const save = () => {
        setSaving(true);
        setError('');
        saveTrackingCouriers(storeId, rows.map((r) => ({ carrier: r.carrier, courier: values[r.carrier] || '' })))
            .then((res) => onSaved?.(res.data?.message || 'Couriers saved.'))
            .catch((err) => setError(err.response?.data?.detail || 'Could not save the couriers.'))
            .finally(() => setSaving(false));
    };
    return (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
            <div className="fixed inset-0 bg-black/50 backdrop-blur-xs" onClick={onClose} aria-hidden="true" />
            <div className="relative flex max-h-[90vh] w-full max-w-lg flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl dark:border-slate-700 dark:bg-slate-900">
                <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4 dark:border-slate-700">
                    <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">Couriers{data?.label ? ` for ${data.label}` : ''}</h2>
                    <button type="button" className="rounded-md p-2 text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800" onClick={onClose}>
                        <X className="h-5 w-5" />
                    </button>
                </div>
                <div className="space-y-3 overflow-y-auto px-6 py-4">
                    <p className="text-sm text-slate-600 dark:text-slate-400">
                        Type the courier exactly as it is in the {data?.label || 'marketplace'} courier list. Only carriers with a
                        courier here are uploaded. Leave it blank to never upload that carrier.
                    </p>
                    {error && <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:border-rose-800 dark:bg-rose-900/20 dark:text-rose-300">{error}</p>}
                    {!data && !error && <p className="text-sm text-slate-500">Loading…</p>}
                    {rows.length > 0 && (
                        <table className="w-full text-sm">
                            <thead className="text-left text-xs uppercase text-slate-500 dark:text-slate-400">
                                <tr><th className="pb-2 font-medium">Carrier on Amazon</th><th className="pb-2 font-medium">{data.label} courier</th></tr>
                            </thead>
                            <tbody>
                                {rows.map((r) => (
                                    <tr key={r.carrier} className="border-t border-slate-100 dark:border-slate-800">
                                        <td className="py-1.5 pr-3 text-slate-700 dark:text-slate-300">{r.carrier}</td>
                                        <td className="py-1.5">
                                            <div className="flex items-center gap-1.5">
                                                <input
                                                    value={values[r.carrier] || ''}
                                                    onChange={(e) => setValues((v) => ({ ...v, [r.carrier]: e.target.value }))}
                                                    placeholder={r.suggestion ? `e.g. ${r.suggestion}` : 'not uploaded'}
                                                    className="w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-sm text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
                                                />
                                                {r.suggestion && !values[r.carrier] && (
                                                    <button
                                                        type="button"
                                                        className="shrink-0 text-xs font-medium text-accent-600 hover:underline dark:text-accent-400"
                                                        onClick={() => setValues((v) => ({ ...v, [r.carrier]: r.suggestion }))}
                                                        title="Fill in the suggested name; check it is in the marketplace's list before saving"
                                                    >
                                                        Use {r.suggestion}
                                                    </button>
                                                )}
                                            </div>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                </div>
                <div className="flex justify-end gap-2 border-t border-slate-200 px-6 py-3 dark:border-slate-700">
                    <Button variant="secondary" onClick={onClose} disabled={saving}>Cancel</Button>
                    <Button variant="primary" onClick={save} disabled={saving || !rows.length}>{saving ? 'Saving…' : 'Save couriers'}</Button>
                </div>
            </div>
        </div>
    );
}

const SKIP_LABEL = { tba: 'TBA number', courier: 'Courier not saved', name: 'Name differs', extra: 'Second number' };

/** Upload tracking: check what goes to Kogan / Big W, then one job uploads all the ready numbers. */
export function UploadTrackingModal({ open, storeId, trackingOnline = null, onClose, onQueued }) {
    const [plan, setPlan] = useState(null);
    const [error, setError] = useState('');
    const [sending, setSending] = useState(false);
    const [couriersOpen, setCouriersOpen] = useState(false);
    const [note, setNote] = useState('');

    const load = () => {
        setPlan(null);
        setError('');
        getUploadTrackingPlan(storeId)
            .then((res) => setPlan(res.data))
            .catch((err) => setError(err.response?.data?.detail || 'Could not load the tracking to upload.'));
    };
    useEffect(() => {
        if (open && storeId) {
            setNote('');
            load();
        }
    }, [open, storeId]); // eslint-disable-line react-hooks/exhaustive-deps

    if (!open) return null;
    const ready = plan?.ready || [];
    const skipped = plan?.skipped || [];
    const label = plan?.label || 'the marketplace';
    const confirm = () => {
        setSending(true);
        setError('');
        startUploadTracking(storeId, ready.map((r) => r.tracking_id))
            .then((res) => onQueued?.(res.data?.message || 'Uploading the tracking on the laptop.'))
            .catch((err) => setError(err.response?.data?.detail || 'Could not start the upload.'))
            .finally(() => setSending(false));
    };
    const section = 'mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400';
    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="fixed inset-0 bg-black/50 backdrop-blur-xs" onClick={onClose} aria-hidden="true" />
            <div className="relative flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl dark:border-slate-700 dark:bg-slate-900">
                <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4 dark:border-slate-700">
                    <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">Upload tracking to {label}</h2>
                    <button type="button" className="rounded-md p-2 text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800" onClick={onClose}>
                        <X className="h-5 w-5" />
                    </button>
                </div>
                <div className="space-y-4 overflow-y-auto px-6 py-4 text-sm">
                    {note && <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-900/20 dark:text-emerald-300">{note}</p>}
                    {error && <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-rose-700 dark:border-rose-800 dark:bg-rose-900/20 dark:text-rose-300">{error}</p>}
                    {!plan && !error && <p className="text-slate-500">Loading…</p>}
                    {plan && (
                        <>
                            <div>
                                <p className={section}>Ready to upload ({ready.length})</p>
                                {ready.length ? (
                                    <ul className="divide-y divide-slate-100 rounded-md border border-slate-200 dark:divide-slate-800 dark:border-slate-700">
                                        {ready.map((r) => (
                                            <li key={r.tracking_id} className="flex flex-wrap items-center gap-x-3 gap-y-0.5 px-3 py-1.5">
                                                <span className="w-28 font-medium text-slate-900 dark:text-slate-100">{r.invoice}</span>
                                                <span className="font-mono text-xs text-slate-700 dark:text-slate-300">{r.tracking_number}</span>
                                                <span className="ml-auto text-xs text-slate-500 dark:text-slate-400">
                                                    {r.carrier_name} → <span className="font-medium text-slate-700 dark:text-slate-200">{r.courier}</span>
                                                    {r.failed_before ? ' · failed before' : ''}
                                                </span>
                                            </li>
                                        ))}
                                    </ul>
                                ) : (
                                    <p className="text-slate-500 dark:text-slate-400">Nothing is ready to upload.</p>
                                )}
                            </div>
                            {skipped.length > 0 && (
                                <div>
                                    <p className={section}>Not uploaded ({skipped.length})</p>
                                    <ul className="divide-y divide-slate-100 rounded-md border border-slate-200 dark:divide-slate-800 dark:border-slate-700">
                                        {skipped.map((r) => (
                                            <li key={r.tracking_id} className="flex flex-wrap items-center gap-x-3 gap-y-0.5 px-3 py-1.5">
                                                <span className="w-28 font-medium text-slate-900 dark:text-slate-100">{r.invoice}</span>
                                                <span className="font-mono text-xs text-slate-700 dark:text-slate-300">{r.tracking_number}</span>
                                                <span className="ml-auto text-xs">
                                                    <span className={r.kind === 'courier' ? 'font-medium text-amber-600 dark:text-amber-400' : 'text-slate-500 dark:text-slate-400'}>
                                                        {SKIP_LABEL[r.kind] || 'Not uploaded'}
                                                    </span>
                                                    <span className="text-slate-500 dark:text-slate-400"> · {r.reason}</span>
                                                </span>
                                            </li>
                                        ))}
                                    </ul>
                                </div>
                            )}
                            {(plan.already > 0 || plan.uploading > 0) && (
                                <p className="text-xs text-slate-500 dark:text-slate-400">
                                    {plan.already > 0 ? `${plan.already} order(s) already on ${label}. ` : ''}
                                    {plan.uploading > 0 ? `${plan.uploading} order(s) being uploaded now.` : ''}
                                </p>
                            )}
                            {trackingOnline === false && (
                                <p className="text-xs text-amber-600 dark:text-amber-400">The Tracking Bot is offline: the upload starts when its agent runs.</p>
                            )}
                        </>
                    )}
                </div>
                <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 px-6 py-3 dark:border-slate-700">
                    <Button variant="secondary" size="sm" onClick={() => setCouriersOpen(true)}>Couriers</Button>
                    <div className="flex gap-2">
                        <Button variant="secondary" onClick={onClose} disabled={sending}>Cancel</Button>
                        <Button variant="primary" onClick={confirm} disabled={sending || !ready.length}>
                            <UploadCloud className="mr-1.5 h-4 w-4" />
                            {sending ? 'Sending…' : `Upload ${ready.length}`}
                        </Button>
                    </div>
                </div>
            </div>
            <CouriersModal
                open={couriersOpen}
                storeId={storeId}
                onClose={() => setCouriersOpen(false)}
                onSaved={(msg) => { setCouriersOpen(false); setNote(msg); load(); }}
            />
        </div>
    );
}
