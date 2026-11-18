import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Loader2, X, XCircle } from 'lucide-react';
import Button from '../ui/Button';
import {
    cancelMydealUploadRun,
    getMydealTemplateStatus,
    getMydealUploadRun,
    listMydealUploadRuns,
    startMydealUploadRun,
} from '../../services/catalogService';

const ACTIVE = ['pending', 'running'];
const POLL_MS = 4000;

const errorText = (err, fallback) => {
    const d = err?.response?.data;
    return d?.error || d?.detail || err?.message || fallback;
};

const when = (iso) => (iso ? new Date(iso).toLocaleString() : '');

const STATUS_STYLE = {
    pending: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200',
    running: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
    finished: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
    failed: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
    cancelled: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
};

const RESULT_LABEL = {
    completed: 'COMPLETED',
    completed_with_errors: 'Completed with errors',
    failed: 'Failed',
    pending: 'Still processing',
    not_found: 'Not on dashboard',
};

function resultStatus(r) {
    if (r.status === 'dry_run') return { text: 'Dry run (not imported)', tone: 'text-slate-500' };
    if (r.status === 'rejected') return { text: `Refused: ${r.message || ''}`, tone: 'text-red-600 dark:text-red-400' };
    if (r.status === 'failed') return { text: `Upload failed: ${r.message || ''}`, tone: 'text-red-600 dark:text-red-400' };
    const label = RESULT_LABEL[r.dash_state] || 'Uploaded';
    const errs = r.dash_state === 'completed_with_errors' && r.error_count ? ` (${r.error_count})` : '';
    const tone = r.dash_state === 'completed'
        ? 'text-emerald-600 dark:text-emerald-400'
        : r.dash_state === 'pending' ? 'text-blue-600 dark:text-blue-400' : 'text-amber-600 dark:text-amber-400';
    return { text: `${label}${errs}`, tone };
}

function StatusBadge({ run }) {
    return (
        <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[run.status] || STATUS_STYLE.pending}`}>
            {ACTIVE.includes(run.status) && <Loader2 className="h-3 w-3 animate-spin" />}
            {run.status_label || run.status}
        </span>
    );
}

function RunPanel({ run, onCancel, cancelling }) {
    const logRef = useRef(null);
    const logTail = useMemo(() => (run.log || '').split('\n').slice(-80).join('\n'), [run.log]);
    useEffect(() => {
        if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
    }, [logTail]);
    const waitingLong = run.status === 'pending' && run.started_at
        && Date.now() - new Date(run.started_at).getTime() > 60000;
    const kinds = (run.kinds || []).map((k) => (k === 'inventory' ? 'Inventory' : 'Price')).join(' + ');

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2 text-sm">
                    <StatusBadge run={run} />
                    <span className="text-slate-600 dark:text-slate-300">{kinds}{run.dry_run ? ' · dry run' : ''}</span>
                    <span className="text-xs text-slate-400">started {when(run.started_at)}{run.laptop ? ` · laptop ${run.laptop}` : ''}</span>
                </div>
                {ACTIVE.includes(run.status) && (
                    <Button variant="secondary" size="sm" onClick={onCancel} disabled={cancelling}>
                        {cancelling ? 'Cancelling…' : 'Cancel'}
                    </Button>
                )}
            </div>
            <p className="text-xs text-slate-500 dark:text-slate-400">
                Vendors: {(run.vendors || []).map((v) => (v.id === 'unmapped' ? 'Unmapped SKUs' : v.name)).join(', ')}
            </p>

            {run.attention && (
                <div className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <span><strong>Action needed on the laptop:</strong> {run.attention}</span>
                </div>
            )}
            {waitingLong && (
                <p className="text-xs text-amber-600 dark:text-amber-400">
                    No laptop has picked this up yet. Is run_agent.bat open on the inventory bot laptop?
                </p>
            )}
            {run.message && !ACTIVE.includes(run.status) && (
                <p className="text-sm text-slate-700 dark:text-slate-200">{run.message}</p>
            )}

            {(run.results || []).length > 0 && (
                <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
                    <table className="min-w-full text-xs">
                        <thead className="bg-slate-50 dark:bg-slate-800/60 text-slate-500 dark:text-slate-400">
                            <tr>
                                <th className="px-2 py-1.5 text-left font-medium">Vendor</th>
                                <th className="px-2 py-1.5 text-left font-medium">File</th>
                                <th className="px-2 py-1.5 text-right font-medium">Rows</th>
                                <th className="px-2 py-1.5 text-left font-medium">Status</th>
                                <th className="px-2 py-1.5 text-left font-medium">Time</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                            {run.results.map((r, i) => {
                                const s = resultStatus(r);
                                const part = Number(r.parts) > 1 ? ` (${r.part}/${r.parts})` : '';
                                return (
                                    <tr key={`${r.vendor}-${r.kind}-${r.part}-${i}`}>
                                        <td className="px-2 py-1.5 text-slate-800 dark:text-slate-100">{r.vendor}</td>
                                        <td className="px-2 py-1.5">{r.kind === 'inventory' ? 'Inventory' : 'Price'}{part}</td>
                                        <td className="px-2 py-1.5 text-right">{r.rows}</td>
                                        <td className={`px-2 py-1.5 ${s.tone}`} title={r.request_id ? `Request ${r.request_id}` : ''}>{s.text}</td>
                                        <td className="px-2 py-1.5 whitespace-nowrap text-slate-500">{r.requested_at || r.uploaded_at}</td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}

            {logTail && (
                <pre ref={logRef} className="max-h-56 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-950 p-3 text-[11px] leading-relaxed text-slate-200">
                    {logTail}
                </pre>
            )}
        </div>
    );
}

export default function MydealMarketplaceUploadModal({ open, onClose, storeId, storeName }) {
    const [vendors, setVendors] = useState([]);
    const [vendorsLoading, setVendorsLoading] = useState(false);
    const [picked, setPicked] = useState({});
    const [kinds, setKinds] = useState({ inventory: true, price: true });
    const [dryRun, setDryRun] = useState(false);
    const [runs, setRuns] = useState([]);
    const [viewRun, setViewRun] = useState(null);
    const [starting, setStarting] = useState(false);
    const [cancelling, setCancelling] = useState(false);
    const [error, setError] = useState('');
    const storeRef = useRef(storeId);
    storeRef.current = storeId;

    const loadRuns = useCallback(() => {
        const sid = storeId;
        if (!sid) return Promise.resolve([]);
        return listMydealUploadRuns(sid).then((r) => {
            if (storeRef.current !== sid) return [];
            const list = r.data?.runs || [];
            setRuns(list);
            return list;
        });
    }, [storeId]);

    // Open: load vendors and runs; show the active run if there is one. Finished runs are in the Logs tab.
    useEffect(() => {
        if (!open || !storeId) return;
        const sid = storeId;
        setError('');
        setViewRun(null);
        setVendorsLoading(true);
        getMydealTemplateStatus(sid, { withVendors: true })
            .then((r) => {
                if (storeRef.current !== sid) return;
                const list = r.data?.vendors || [];
                setVendors(list);
                setPicked(Object.fromEntries(list.map((v) => [v.id, v.id !== 'unmapped'])));
            })
            .catch((err) => setError(errorText(err, 'Could not load vendors')))
            .finally(() => setVendorsLoading(false));
        loadRuns()
            .then((list) => {
                const active = list.find((r) => ACTIVE.includes(r.status));
                if (active) setViewRun(active);
            })
            .catch(() => {});
    }, [open, storeId, loadRuns]);

    // Poll the run being shown while it is active (and once more when it ends).
    const viewId = viewRun?.id;
    const viewActive = viewRun ? ACTIVE.includes(viewRun.status) : false;
    useEffect(() => {
        if (!open || !storeId || !viewId) return undefined;
        let stop = false;
        const sid = storeId;
        const tick = () => getMydealUploadRun(sid, viewId)
            .then((r) => {
                if (stop || storeRef.current !== sid) return;
                setViewRun(r.data);
                if (!ACTIVE.includes(r.data?.status)) loadRuns().catch(() => {});
            })
            .catch(() => {});
        tick();
        if (!viewActive) return () => { stop = true; };
        const timer = setInterval(tick, POLL_MS);
        return () => { stop = true; clearInterval(timer); };
    }, [open, storeId, viewId, viewActive, loadRuns]);

    if (!open) return null;

    const chosenVendors = vendors.filter((v) => picked[v.id]);
    const chosenKinds = ['inventory', 'price'].filter((k) => kinds[k]);
    const rowsFor = (v) => chosenKinds.reduce((n, k) => n + (k === 'inventory' ? v.inventory_rows : v.price_rows), 0);
    const totalFiles = chosenVendors.reduce((n, v) => n + chosenKinds.filter((k) => (k === 'inventory' ? v.inventory_rows : v.price_rows) > 0).length, 0);
    const allPicked = vendors.length > 0 && vendors.filter((v) => v.id !== 'unmapped').every((v) => picked[v.id]);
    const activeRun = runs.find((r) => ACTIVE.includes(r.status));

    const start = () => {
        setStarting(true);
        setError('');
        startMydealUploadRun(storeId, { vendors: chosenVendors.map((v) => v.id), kinds: chosenKinds, dryRun })
            .then((r) => {
                setViewRun(r.data);
                loadRuns().catch(() => {});
            })
            .catch((err) => setError(errorText(err, 'Could not start the upload')))
            .finally(() => setStarting(false));
    };

    const cancel = () => {
        if (!viewRun) return;
        setCancelling(true);
        cancelMydealUploadRun(storeId, viewRun.id)
            .then((r) => {
                setViewRun((prev) => ({ ...prev, ...r.data }));
                loadRuns().catch(() => {});
            })
            .catch((err) => setError(errorText(err, 'Could not cancel')))
            .finally(() => setCancelling(false));
    };

    const toggleAll = () => {
        const next = !allPicked;
        setPicked(Object.fromEntries(vendors.map((v) => [v.id, v.id === 'unmapped' ? false : next])));
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" role="dialog" aria-modal="true">
            <div className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-xl border border-slate-200 bg-white shadow-xl dark:border-slate-700 dark:bg-slate-900">
                <div className="flex shrink-0 items-center justify-between border-b border-slate-200 px-5 py-4 dark:border-slate-700">
                    <div>
                        <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">Upload the file on the Marketplace</h2>
                        <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                            Store: {storeName || 'MyDeal'}. The inventory bot laptop uploads each vendor&apos;s file to MyDeal, one at a time. Each upload&apos;s result is written to the Logs tab.
                        </p>
                    </div>
                    <button type="button" onClick={onClose} className="rounded-md p-1 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800" aria-label="Close">
                        <X className="h-5 w-5" />
                    </button>
                </div>

                <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
                    {error && (
                        <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300">
                            <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
                            <span>{error}</span>
                        </div>
                    )}

                    {viewRun ? (
                        <div className="space-y-3">
                            <RunPanel run={viewRun} onCancel={cancel} cancelling={cancelling} />
                            {!ACTIVE.includes(viewRun.status) && (
                                <Button variant="secondary" size="sm" onClick={() => setViewRun(null)}>New upload</Button>
                            )}
                        </div>
                    ) : (
                        <div className="space-y-4">
                            <section>
                                <h3 className="mb-2 text-sm font-medium text-slate-800 dark:text-slate-100">What to update</h3>
                                <div className="flex flex-wrap gap-4 text-sm">
                                    {[['inventory', 'Inventory (Stock/Status)'], ['price', 'Price']].map(([k, label]) => (
                                        <label key={k} className="flex items-center gap-2">
                                            <input type="checkbox" checked={kinds[k]} onChange={(e) => setKinds((p) => ({ ...p, [k]: e.target.checked }))} />
                                            {label}
                                        </label>
                                    ))}
                                </div>
                            </section>

                            <section>
                                <div className="mb-2 flex items-center justify-between">
                                    <h3 className="text-sm font-medium text-slate-800 dark:text-slate-100">Vendors</h3>
                                    {vendors.length > 0 && (
                                        <button type="button" className="text-xs text-blue-600 hover:underline dark:text-blue-400" onClick={toggleAll}>
                                            {allPicked ? 'Clear all' : 'Select all'}
                                        </button>
                                    )}
                                </div>
                                {vendorsLoading ? (
                                    <p className="text-xs text-slate-400">Loading vendors…</p>
                                ) : vendors.length === 0 ? (
                                    <p className="text-xs text-slate-400">No template rows yet. Upload the MyDeal templates first.</p>
                                ) : (
                                    <div className="max-h-60 divide-y divide-slate-100 overflow-y-auto rounded-lg border border-slate-200 dark:divide-slate-800 dark:border-slate-700">
                                        {vendors.map((v) => (
                                            <label key={v.id} className="flex cursor-pointer items-center justify-between gap-3 px-3 py-2 text-sm hover:bg-slate-50 dark:hover:bg-slate-800/50">
                                                <span className="flex items-center gap-2">
                                                    <input type="checkbox" checked={!!picked[v.id]} onChange={(e) => setPicked((p) => ({ ...p, [v.id]: e.target.checked }))} />
                                                    <span className={v.id === 'unmapped' ? 'italic text-amber-600 dark:text-amber-400' : 'text-slate-800 dark:text-slate-100'}>
                                                        {v.id === 'unmapped' ? 'Unmapped SKUs' : v.name}
                                                    </span>
                                                </span>
                                                <span className="text-xs text-slate-400">{v.inventory_rows} inv / {v.price_rows} price</span>
                                            </label>
                                        ))}
                                    </div>
                                )}
                                {picked.unmapped && (
                                    <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">
                                        Unmapped files have blank prices and can mark products Discontinued on MyDeal.
                                    </p>
                                )}
                            </section>

                            <label className="flex items-center gap-2 text-sm">
                                <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
                                Dry run (go through every step but do not click Import)
                            </label>

                            <div className="flex flex-wrap items-center gap-3">
                                <Button
                                    variant="primary"
                                    onClick={start}
                                    disabled={starting || !!activeRun || !chosenKinds.length || !chosenVendors.length || totalFiles === 0}
                                >
                                    {starting ? 'Starting…' : 'Start upload'}
                                </Button>
                                <span className="text-xs text-slate-500 dark:text-slate-400">
                                    {totalFiles} file(s), {chosenVendors.reduce((n, v) => n + rowsFor(v), 0)} rows
                                </span>
                                {activeRun && (
                                    <button type="button" className="text-xs text-blue-600 hover:underline dark:text-blue-400" onClick={() => setViewRun(activeRun)}>
                                        An upload is already {activeRun.status === 'pending' ? 'waiting' : 'running'}: view it
                                    </button>
                                )}
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
