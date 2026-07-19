import { useEffect, useRef, useState } from 'react';
import { Download, FileDown, Search, Square } from 'lucide-react';
import Modal from '../ui/Modal';
import Input from '../ui/Input';
import Button from '../ui/Button';
import Badge from '../design/Badge';
import {
    MARKETPLACE_LOOKUP_MAX,
    cancelMarketplaceLookupJob,
    downloadMarketplaceLookupJobCsv,
    getMarketplaceLookupProgress,
    lookupListingOnMarketplace,
    startMarketplaceLookupJob,
} from '../../services/listingService';

function formatWhen(value) {
    if (!value) return '—';
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toLocaleString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    });
}

function apiError(err, fallback) {
    const detail = err.response?.data?.detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
    return err.message || fallback;
}

function statusBadgeVariant(status) {
    if (status === 'done') return 'success';
    if (status === 'cancelled') return 'warning';
    if (status === 'error') return 'error';
    if (status === 'running' || status === 'queued') return 'accent';
    return 'default';
}

export default function MarketplaceLookupModal({ open, onClose, storeId, storeName }) {
    const [mode, setMode] = useState('single'); // single | bulk
    const [sku, setSku] = useState('');
    const [bulkText, setBulkText] = useState('');
    const [bulkFile, setBulkFile] = useState(null);
    const [loading, setLoading] = useState(false);
    const [cancelling, setCancelling] = useState(false);
    const [downloading, setDownloading] = useState(false);
    const [error, setError] = useState('');
    const [result, setResult] = useState(null);
    const [job, setJob] = useState(null);
    const fileRef = useRef(null);

    useEffect(() => {
        if (!open) return;
        setMode('single');
        setSku('');
        setBulkText('');
        setBulkFile(null);
        setError('');
        setResult(null);
        setLoading(false);
        setCancelling(false);
        setDownloading(false);
        if (fileRef.current) fileRef.current.value = '';
        // Keep last job status when reopening — loaded by the poll effect below.
    }, [open, storeId]);

    // Poll job progress while modal is open (also restores finished results after leave).
    useEffect(() => {
        if (!open || !storeId) return undefined;
        let cancelled = false;
        let restoredTab = false;
        const tick = () => {
            getMarketplaceLookupProgress(storeId)
                .then((res) => {
                    if (cancelled) return;
                    const data = res.data || null;
                    setJob(data);
                    if (!restoredTab && data && (data.active || data.has_results)) {
                        setMode('bulk');
                        restoredTab = true;
                    }
                })
                .catch(() => { /* ignore transient poll errors */ });
        };
        tick();
        const id = setInterval(tick, 2000);
        return () => {
            cancelled = true;
            clearInterval(id);
        };
    }, [open, storeId]);

    const handleSingleSearch = (e) => {
        e.preventDefault();
        const q = sku.trim();
        if (!q || !storeId) {
            setError('Enter a SKU to search.');
            return;
        }
        setLoading(true);
        setError('');
        setResult(null);
        lookupListingOnMarketplace(storeId, q)
            .then((res) => setResult(res.data || null))
            .catch((err) => setError(apiError(err, 'Could not check the marketplace.')))
            .finally(() => setLoading(false));
    };

    const handleBulkSearch = (e) => {
        e.preventDefault();
        if (!storeId) return;
        if (!bulkFile && !bulkText.trim()) {
            setError('Paste SKUs (one per line) or upload a file.');
            return;
        }
        setLoading(true);
        setError('');
        setResult(null);
        const payload = bulkFile ? { file: bulkFile } : { text: bulkText };
        startMarketplaceLookupJob(storeId, payload)
            .then((res) => {
                setJob(res.data || null);
                setMode('bulk');
            })
            .catch((err) => setError(apiError(err, 'Could not start marketplace check.')))
            .finally(() => setLoading(false));
    };

    const handleCancel = () => {
        if (!storeId || cancelling) return;
        setCancelling(true);
        setError('');
        cancelMarketplaceLookupJob(storeId)
            .then((res) => setJob(res.data || null))
            .catch((err) => setError(apiError(err, 'Could not cancel.')))
            .finally(() => setCancelling(false));
    };

    const handleDownloadTemplate = () => {
        const content = [
            'SKU',
            'YOUR-SKU-001',
            'YOUR-SKU-002',
            'YOUR-SKU-003',
        ].join('\n');
        const blob = new Blob(['\uFEFF' + content + '\n'], { type: 'text/csv;charset=utf-8' });
        const url = window.URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', 'marketplace_sku_search_template.csv');
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
    };

    const handleDownloadCsv = () => {
        if (!storeId) return;
        setDownloading(true);
        setError('');
        downloadMarketplaceLookupJobCsv(storeId)
            .catch((err) => setError(apiError(err, 'Could not download CSV.')))
            .finally(() => setDownloading(false));
    };

    const hit = Array.isArray(result?.results) ? result.results[0] : null;
    const local = result?.local_listing || null;
    const jobActive = Boolean(job?.active);
    const jobHasResults = Boolean(job?.has_results);
    const progressPct = Number(job?.pct || 0);
    const showJobPanel = job && job.status && job.status !== 'idle';

    return (
        <Modal open={open} onClose={onClose} title="Check on marketplace">
            <p className="text-sm text-slate-500 dark:text-slate-400 mb-3">
                Search {storeName || 'this store'} live on the marketplace by SKU / variant key.
            </p>

            <div className="mb-4 inline-flex rounded-md border border-slate-200 dark:border-slate-600 p-0.5 bg-slate-50 dark:bg-slate-800">
                <button
                    type="button"
                    onClick={() => { setMode('single'); setError(''); }}
                    className={`rounded px-3 py-1.5 text-xs font-semibold transition ${
                        mode === 'single'
                            ? 'bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 shadow-sm'
                            : 'text-slate-500 dark:text-slate-400'
                    }`}
                >
                    Single SKU
                </button>
                <button
                    type="button"
                    onClick={() => { setMode('bulk'); setError(''); }}
                    className={`rounded px-3 py-1.5 text-xs font-semibold transition ${
                        mode === 'bulk'
                            ? 'bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 shadow-sm'
                            : 'text-slate-500 dark:text-slate-400'
                    }`}
                >
                    Bulk search
                </button>
            </div>

            {mode === 'single' ? (
                <form onSubmit={handleSingleSearch} className="space-y-4">
                    <Input
                        label="SKU"
                        value={sku}
                        onChange={(e) => setSku(e.target.value)}
                        placeholder="e.g. JJ-XH1899BK-FCBY"
                        autoFocus
                    />
                    {error ? (
                        <p className="text-sm text-rose-600 dark:text-rose-400">{error}</p>
                    ) : null}
                    <div className="flex justify-end gap-2">
                        <Button type="button" variant="secondary" size="sm" onClick={onClose}>
                            Close
                        </Button>
                        <Button type="submit" variant="primary" size="sm" disabled={loading}>
                            <Search className="h-4 w-4 mr-1.5" />
                            {loading ? 'Checking…' : 'Search'}
                        </Button>
                    </div>
                </form>
            ) : (
                <form onSubmit={handleBulkSearch} className="space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
                            SKUs (one per line, or comma-separated)
                        </label>
                        <textarea
                            value={bulkText}
                            onChange={(e) => {
                                setBulkText(e.target.value);
                                setBulkFile(null);
                                if (fileRef.current) fileRef.current.value = '';
                            }}
                            rows={5}
                            placeholder={"SKU-001\nSKU-002\nSKU-003"}
                            className="w-full rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 focus:border-accent-500 focus:ring-1 focus:ring-accent-500 outline-none"
                            disabled={loading || jobActive}
                        />
                        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                            Max {MARKETPLACE_LOOKUP_MAX.toLocaleString()} SKUs per run.
                            You can leave this page — the check continues in the background.
                            Starting a new check replaces the previous results.
                        </p>
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
                            Upload file (optional)
                        </label>
                        <div className="flex flex-wrap items-center gap-2 mb-2">
                            <Button
                                type="button"
                                variant="secondary"
                                size="sm"
                                onClick={handleDownloadTemplate}
                                disabled={loading || jobActive}
                            >
                                <Download className="h-4 w-4 mr-1.5" />
                                Download template
                            </Button>
                            <span className="text-xs text-slate-500 dark:text-slate-400">
                                CSV with a <span className="font-medium">SKU</span> column (also accepts .txt / .xlsx)
                            </span>
                        </div>
                        <input
                            ref={fileRef}
                            type="file"
                            accept=".txt,.csv,.xlsx,.xlsm"
                            disabled={loading || jobActive}
                            onChange={(e) => {
                                const f = e.target.files?.[0] || null;
                                setBulkFile(f);
                                if (f) setBulkText('');
                            }}
                            className="block w-full text-sm text-slate-600 dark:text-slate-300 file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 dark:file:bg-slate-700 file:px-3 file:py-1.5 file:text-sm"
                        />
                        {bulkFile ? (
                            <p className="mt-1 text-xs text-slate-500">Selected: {bulkFile.name}</p>
                        ) : null}
                    </div>
                    {error ? (
                        <p className="text-sm text-rose-600 dark:text-rose-400">{error}</p>
                    ) : null}
                    <div className="flex flex-wrap justify-end gap-2">
                        <Button type="button" variant="secondary" size="sm" onClick={onClose}>
                            Close
                        </Button>
                        {jobActive ? (
                            <Button
                                type="button"
                                variant="secondary"
                                size="sm"
                                disabled={cancelling}
                                onClick={handleCancel}
                            >
                                <Square className="h-3.5 w-3.5 mr-1.5" />
                                {cancelling ? 'Cancelling…' : 'Cancel'}
                            </Button>
                        ) : null}
                        <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            disabled={downloading || !jobHasResults}
                            onClick={handleDownloadCsv}
                        >
                            <FileDown className="h-4 w-4 mr-1.5" />
                            {downloading ? 'Downloading…' : 'Download CSV'}
                        </Button>
                        <Button type="submit" variant="primary" size="sm" disabled={loading || jobActive}>
                            <Search className="h-4 w-4 mr-1.5" />
                            {loading ? 'Starting…' : jobActive ? 'Running…' : 'Run bulk check'}
                        </Button>
                    </div>
                </form>
            )}

            {mode === 'bulk' && showJobPanel ? (
                <div className="mt-5 space-y-3 border-t border-slate-200 dark:border-slate-700 pt-4">
                    <div className="flex flex-wrap items-center gap-2">
                        <Badge variant={statusBadgeVariant(job.status)}>
                            {job.status}
                        </Badge>
                        {jobActive ? (
                            <Badge variant="accent">
                                {(job.processed || 0).toLocaleString()} / {(job.total || 0).toLocaleString()}
                            </Badge>
                        ) : null}
                        <Badge variant="success">{job.found ?? 0} found</Badge>
                        <Badge variant="warning">{job.not_found ?? 0} not found</Badge>
                        {(job.errors ?? 0) > 0 ? (
                            <Badge variant="error">{job.errors} errors</Badge>
                        ) : null}
                    </div>
                    <p className="text-sm text-slate-700 dark:text-slate-300">{job.message}</p>
                    {jobActive ? (
                        <div>
                            <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
                                <div
                                    className="h-full rounded-full bg-sky-500 transition-all duration-300"
                                    style={{ width: `${Math.max(progressPct, job.total ? 2 : 0)}%` }}
                                />
                            </div>
                            {job.current_sku ? (
                                <p className="mt-1 text-xs text-slate-500 truncate" title={job.current_sku}>
                                    Current: {job.current_sku}
                                </p>
                            ) : null}
                        </div>
                    ) : null}
                    {jobHasResults ? (
                        <p className="text-xs text-slate-500 dark:text-slate-400">
                            Results stay available here until you start a new bulk check.
                            Use <span className="font-medium">Download CSV</span> anytime.
                        </p>
                    ) : null}
                </div>
            ) : null}

            {mode === 'single' && result ? (
                <div className="mt-5 space-y-4 border-t border-slate-200 dark:border-slate-700 pt-4">
                    <div className="flex flex-wrap items-center gap-2">
                        <Badge variant={result.found ? 'success' : 'warning'}>
                            {result.found ? 'Found on marketplace' : 'Not found'}
                        </Badge>
                        <span className="text-xs uppercase tracking-wide text-slate-400">
                            {result.marketplace}
                            {result.environment ? ` · ${result.environment}` : ''}
                        </span>
                    </div>
                    <p className="text-sm text-slate-700 dark:text-slate-300">{result.message}</p>

                    {hit ? (
                        <dl className="grid grid-cols-1 gap-2 text-sm">
                            <div className="flex justify-between gap-3">
                                <dt className="text-slate-500 dark:text-slate-400">SKU / variant</dt>
                                <dd className="font-medium text-slate-900 dark:text-slate-100 text-right">
                                    {hit.sku || hit.variant_key || '—'}
                                </dd>
                            </div>
                            {hit.title ? (
                                <div className="flex justify-between gap-3">
                                    <dt className="text-slate-500 dark:text-slate-400">Title</dt>
                                    <dd className="text-slate-900 dark:text-slate-100 text-right max-w-[60%]">
                                        {hit.title}
                                    </dd>
                                </div>
                            ) : null}
                            <div className="flex justify-between gap-3">
                                <dt className="text-slate-500 dark:text-slate-400">Status</dt>
                                <dd className="font-medium text-slate-900 dark:text-slate-100 capitalize">
                                    {hit.status || '—'}
                                </dd>
                            </div>
                            <div className="flex justify-between gap-3">
                                <dt className="text-slate-500 dark:text-slate-400">Created</dt>
                                <dd className="text-slate-900 dark:text-slate-100">
                                    {formatWhen(hit.created_at || hit.published_at)}
                                </dd>
                            </div>
                            {hit.url ? (
                                <div className="pt-1">
                                    <a
                                        href={hit.url}
                                        target="_blank"
                                        rel="noreferrer"
                                        className="text-sm font-medium text-accent-600 dark:text-accent-400 hover:underline"
                                    >
                                        Open on marketplace
                                    </a>
                                </div>
                            ) : null}
                        </dl>
                    ) : null}

                    {local ? (
                        <div className="rounded-md border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/50 px-3 py-2 text-xs text-slate-600 dark:text-slate-300">
                            In Seller Pilot Hub: status <span className="font-medium">{local.status}</span>
                            {local.created_at ? ` · created ${formatWhen(local.created_at)}` : ''}
                        </div>
                    ) : (
                        <p className="text-xs text-slate-500 dark:text-slate-400">
                            This SKU is not in Created products for this store.
                        </p>
                    )}
                </div>
            ) : null}
        </Modal>
    );
}
