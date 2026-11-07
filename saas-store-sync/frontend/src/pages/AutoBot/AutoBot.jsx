import { useCallback, useEffect, useState } from 'react';
import { AlertCircle, Bot, Download, FileSpreadsheet, Laptop, Loader2, Play, Square, Trash2, Upload, X } from 'lucide-react';
import Button from '../../components/ui/Button';
import ConfirmModal from '../../components/ui/ConfirmModal';
import Select from '../../components/ui/Select';
import PageHeader from '../../components/design/PageHeader';
import Badge from '../../components/design/Badge';
import EmptyState from '../../components/design/EmptyState';
import {
    cancelAutoBotJob,
    createAutoBotJob,
    deleteAutoBotJob,
    downloadAutoBotFile,
    getAutoBotJobs,
    getAutoBotLaptops,
} from '../../services/autoBotService';

const STATUS_VARIANT = {
    pending: 'warning',
    claimed: 'accent',
    done: 'success',
    failed: 'error',
    cancelled: 'default',
};

const STATUS_LABEL = {
    pending: 'Pending · waiting for a free laptop',
    claimed: 'Running',
    done: 'Done',
    failed: 'Failed',
    cancelled: 'Cancelled',
};

const COLUMN_HINTS = {
    create: 'The create-listing sheet for this marketplace (store_name, title, price, sku, ...).',
    stock: 'Columns: store_name, sku, price, quantity. A blank price or quantity stays unchanged.',
    sku: 'Columns: store_name, sku, new_sku, upc. sku is the current SKU. A blank upc stays unchanged.',
};

const CARD = 'rounded-xl border border-slate-200 bg-white shadow-xs dark:border-slate-700 dark:bg-slate-900';

const DOT = {
    success: 'bg-emerald-500',
    warning: 'bg-amber-500',
    default: 'bg-slate-400',
};

const POLL_MS = 5000;
const ONLINE_MS = 2 * 60 * 1000;

function hintFor(jobType) {
    return COLUMN_HINTS[(jobType || '').split('-')[1]] || '';
}

function formatTime(value) {
    return value ? new Date(value).toLocaleString() : '';
}

function errorText(err, fallback) {
    return err?.response?.data?.error || err?.response?.data?.detail || err?.message || fallback;
}

export default function AutoBot() {
    const [jobTypes, setJobTypes] = useState([]);
    const [jobs, setJobs] = useState([]);
    const [laptops, setLaptops] = useState([]);
    const [now, setNow] = useState(() => Date.now());
    const [jobType, setJobType] = useState('etsy-stock');
    const [storeName, setStoreName] = useState('');
    const [file, setFile] = useState(null);
    const [fileKey, setFileKey] = useState(0);
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState('');
    const [busyId, setBusyId] = useState('');
    const [deleteTarget, setDeleteTarget] = useState(null);
    const [deleting, setDeleting] = useState(false);

    const load = useCallback(async () => {
        try {
            const [jobData, laptopData] = await Promise.all([getAutoBotJobs(), getAutoBotLaptops()]);
            setJobTypes(jobData.job_types || []);
            setJobs(jobData.jobs || []);
            setLaptops(laptopData.laptops || []);
            setNow(laptopData.now ? Date.parse(laptopData.now) : Date.now());
        } catch (err) {
            setError(errorText(err, 'Could not load Auto Bot jobs.'));
        }
    }, []);

    useEffect(() => {
        load();
    }, [load]);

    const active = jobs.some((job) => job.status === 'pending' || job.status === 'claimed');
    useEffect(() => {
        if (!active) return undefined;
        const timer = window.setInterval(load, POLL_MS);
        return () => window.clearInterval(timer);
    }, [active, load]);

    const onSubmit = async (event) => {
        event.preventDefault();
        if (!file) {
            setError('Choose an Excel file.');
            return;
        }
        setSubmitting(true);
        setError('');
        try {
            await createAutoBotJob({ jobType, file, storeName });
            setFile(null);
            setFileKey((key) => key + 1);
            await load();
        } catch (err) {
            setError(errorText(err, 'Could not queue the job.'));
        } finally {
            setSubmitting(false);
        }
    };

    const onCancel = async (job) => {
        setBusyId(job.id);
        setError('');
        try {
            await cancelAutoBotJob(job.id);
            await load();
        } catch (err) {
            setError(errorText(err, 'Could not cancel the job.'));
        } finally {
            setBusyId('');
        }
    };

    const onDelete = async () => {
        if (!deleteTarget) return;
        setDeleting(true);
        setError('');
        try {
            await deleteAutoBotJob(deleteTarget.id);
            setDeleteTarget(null);
            await load();
        } catch (err) {
            setError(errorText(err, 'Could not delete the job.'));
        } finally {
            setDeleting(false);
        }
    };

    const onDownload = (job, kind) => {
        downloadAutoBotFile(job.id, kind).catch((err) => setError(errorText(err, 'Download failed.')));
    };

    return (
        <div className="space-y-6">
            <PageHeader
                title="Auto Bot"
                description="Send a Reverb or Etsy Excel to the laptops. A laptop runs the bot, then posts back the rows that finished and the rows that remain."
            />

            {error && (
                <div className="flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-900/30 dark:text-rose-300">
                    <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                    <span className="flex-1">{error}</span>
                    <button type="button" onClick={() => setError('')} className="shrink-0 opacity-70 hover:opacity-100" aria-label="Dismiss">
                        <X className="h-4 w-4" />
                    </button>
                </div>
            )}

            <div className="grid gap-6 lg:grid-cols-5">
                <form onSubmit={onSubmit} className={`${CARD} flex flex-col lg:col-span-3`}>
                    <div className="border-b border-slate-100 px-5 py-4 dark:border-slate-800">
                        <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">New job</h2>
                        <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">Pick the job, the store and the Excel to run.</p>
                    </div>
                    <div className="flex-1 space-y-5 p-5">
                        <div className="grid gap-5 sm:grid-cols-2">
                            <div>
                                <Select
                                    label="Job"
                                    value={jobType}
                                    onChange={(e) => setJobType(e.target.value)}
                                    options={jobTypes.length ? jobTypes : [{ value: jobType, label: jobType }]}
                                />
                                {hintFor(jobType) && (
                                    <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">{hintFor(jobType)}</p>
                                )}
                            </div>
                            <div>
                                <label htmlFor="autobot-store" className="mb-1 block text-sm font-medium text-slate-700 dark:text-slate-300">
                                    Store <span className="font-normal text-slate-400">(optional)</span>
                                </label>
                                <input
                                    id="autobot-store"
                                    value={storeName}
                                    onChange={(e) => setStoreName(e.target.value)}
                                    placeholder="e.g. AMH"
                                    className="block w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 shadow-xs outline-hidden focus:border-accent-500 focus:ring-1 focus:ring-accent-500 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
                                />
                                <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">
                                    Leave empty to split the file by store_name: one job per store, each on that store&apos;s laptop.
                                </p>
                            </div>
                        </div>

                        <div>
                            <span className="mb-1 block text-sm font-medium text-slate-700 dark:text-slate-300">
                                Excel file <span className="font-normal text-slate-400">(.xlsx)</span>
                            </span>
                            <label
                                htmlFor="autobot-file"
                                className={`flex cursor-pointer items-center gap-3 rounded-lg border border-dashed px-4 py-4 transition ${
                                    file
                                        ? 'border-accent-400 bg-accent-50/50 dark:border-accent-600 dark:bg-accent-900/10'
                                        : 'border-slate-300 hover:border-accent-400 hover:bg-slate-50 dark:border-slate-600 dark:hover:border-accent-500 dark:hover:bg-slate-800/50'
                                }`}
                            >
                                <div className="rounded-md bg-slate-100 p-2 dark:bg-slate-800">
                                    {file ? (
                                        <FileSpreadsheet className="h-5 w-5 text-emerald-600 dark:text-emerald-400" />
                                    ) : (
                                        <Upload className="h-5 w-5 text-slate-500 dark:text-slate-400" />
                                    )}
                                </div>
                                <div className="min-w-0 flex-1">
                                    <p className={`truncate text-sm ${file ? 'font-medium text-slate-900 dark:text-slate-100' : 'text-slate-600 dark:text-slate-300'}`}>
                                        {file ? file.name : 'Click to choose an Excel file'}
                                    </p>
                                    <p className="text-xs text-slate-500 dark:text-slate-400">
                                        {file ? `${(file.size / 1024).toFixed(1)} KB` : 'Only .xlsx files'}
                                    </p>
                                </div>
                                <span className="rounded-md bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-700 dark:bg-slate-800 dark:text-slate-200">
                                    {file ? 'Replace' : 'Browse'}
                                </span>
                            </label>
                            <input
                                id="autobot-file"
                                key={fileKey}
                                type="file"
                                accept=".xlsx"
                                onChange={(e) => setFile(e.target.files?.[0] || null)}
                                className="sr-only"
                            />
                        </div>
                    </div>
                    <div className="flex items-center justify-end border-t border-slate-100 px-5 py-3 dark:border-slate-800">
                        <Button type="submit" disabled={submitting || !file}>
                            {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Play className="mr-2 h-4 w-4" />}
                            Send to laptops
                        </Button>
                    </div>
                </form>

                <section className={`${CARD} lg:col-span-2`}>
                    <div className="border-b border-slate-100 px-5 py-4 dark:border-slate-800">
                        <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Laptops</h2>
                        <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                            Each laptop runs the Etsy and Reverb jobs for its own stores, oldest first.
                        </p>
                    </div>
                    {laptops.length === 0 ? (
                        <p className="px-5 py-6 text-sm text-slate-500 dark:text-slate-400">
                            No laptop is set up yet. Jobs wait here until one starts.
                        </p>
                    ) : (
                        <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                            {laptops.map((laptop) => {
                                const seen = laptop.last_used_at ? Date.parse(laptop.last_used_at) : 0;
                                const online = seen && now - seen < ONLINE_MS;
                                let state;
                                if (online && laptop.busy) state = laptop.job_label ? `Running ${laptop.job_label}` : 'Busy';
                                else if (online) state = 'Free';
                                else if (seen) state = `Offline · last seen ${formatTime(laptop.last_used_at)}`;
                                else state = 'Never connected';
                                const variant = !online ? 'default' : laptop.busy ? 'warning' : 'success';
                                return (
                                    <li key={laptop.label} className="flex items-center gap-3 px-5 py-3">
                                        <div className="relative rounded-md bg-slate-100 p-2 dark:bg-slate-800">
                                            <Laptop className="h-4 w-4 text-slate-600 dark:text-slate-300" />
                                            <span className={`absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full ring-2 ring-white dark:ring-slate-900 ${DOT[variant]}`} />
                                        </div>
                                        <div className="min-w-0 flex-1">
                                            <p className="text-sm font-medium text-slate-900 dark:text-slate-100">{laptop.label}</p>
                                            <p className="truncate text-xs text-slate-500 dark:text-slate-400">
                                                {laptop.stores?.length ? laptop.stores.join(', ') : 'Any store'}
                                            </p>
                                        </div>
                                        <Badge variant={variant} className="max-w-[55%] truncate" title={state}>
                                            {state}
                                        </Badge>
                                    </li>
                                );
                            })}
                        </ul>
                    )}
                </section>
            </div>

            <section className={CARD}>
                <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4 dark:border-slate-800">
                    <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                        Jobs {jobs.length > 0 && <span className="ml-1 font-normal text-slate-400">({jobs.length})</span>}
                    </h2>
                    {active && (
                        <span className="inline-flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
                            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Refreshing every {POLL_MS / 1000}s
                        </span>
                    )}
                </div>
                {jobs.length === 0 ? (
                    <EmptyState
                        icon={Bot}
                        title="No jobs yet"
                        description="Choose a job, upload the Excel and send it. A laptop picks it up on its next check."
                    />
                ) : (
                    <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                        {jobs.map((job) => {
                            const running = job.status === 'pending' || job.status === 'claimed';
                            const hasCounts = job.done_count !== null && job.done_count !== undefined;
                            const total = hasCounts ? job.done_count + (job.remaining_count || 0) : 0;
                            const pct = total ? Math.round((job.done_count / total) * 100) : 0;
                            return (
                                <li key={job.id} className="flex flex-col gap-3 px-5 py-4 lg:flex-row lg:items-center lg:justify-between">
                                    <div className="min-w-0 flex-1">
                                        <div className="flex flex-wrap items-center gap-2">
                                            <span className="text-sm font-medium text-slate-900 dark:text-slate-100">{job.job_label}</span>
                                            {job.store_name && <Badge>{job.store_name}</Badge>}
                                            <Badge variant={STATUS_VARIANT[job.status] || 'default'}>
                                                {STATUS_LABEL[job.status] || job.status}
                                            </Badge>
                                            {job.status === 'pending' && !job.has_laptop && (
                                                <Badge variant="error">No laptop is set up for store {job.store_name}</Badge>
                                            )}
                                            {job.status === 'pending' && job.has_laptop && job.queue_position > 1 && (
                                                <Badge>{job.queue_position - 1} ahead for {job.store_name}</Badge>
                                            )}
                                        </div>
                                        <p className="mt-1 truncate text-xs text-slate-500 dark:text-slate-400">
                                            {job.file_name} · sent {formatTime(job.requested_at)}
                                            {job.laptop ? ` · ${job.laptop}` : ''}
                                            {job.completed_at ? ` · ended ${formatTime(job.completed_at)}` : ''}
                                        </p>
                                        {hasCounts && (
                                            <div className="mt-2 flex max-w-md items-center gap-3">
                                                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                                                    <div
                                                        className={`h-full rounded-full ${job.remaining_count ? 'bg-amber-500' : 'bg-emerald-500'}`}
                                                        style={{ width: `${pct}%` }}
                                                    />
                                                </div>
                                                <span className="shrink-0 text-xs text-slate-500 dark:text-slate-400">
                                                    {job.done_count} finished · {job.remaining_count} remaining
                                                </span>
                                            </div>
                                        )}
                                        {job.note && (
                                            <p className="mt-2 whitespace-pre-line rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
                                                {job.note}
                                            </p>
                                        )}
                                    </div>
                                    <div className="flex flex-wrap items-center gap-2 lg:justify-end">
                                        {job.has_remaining_file && (
                                            <Button size="sm" variant="secondary" onClick={() => onDownload(job, 'remaining')}>
                                                <Download className="mr-1 h-4 w-4" />
                                                Remaining
                                            </Button>
                                        )}
                                        {job.has_done_file && (
                                            <Button size="sm" variant="secondary" onClick={() => onDownload(job, 'done')}>
                                                <Download className="mr-1 h-4 w-4" />
                                                Finished
                                            </Button>
                                        )}
                                        <Button size="sm" variant="secondary" onClick={() => onDownload(job, 'input')}>
                                            <Download className="mr-1 h-4 w-4" />
                                            Original
                                        </Button>
                                        {running && (
                                            <Button size="sm" variant="secondary" disabled={busyId === job.id} onClick={() => onCancel(job)}>
                                                <Square className="mr-1 h-4 w-4" />
                                                Cancel
                                            </Button>
                                        )}
                                        <button
                                            type="button"
                                            onClick={() => setDeleteTarget(job)}
                                            title="Delete job"
                                            aria-label="Delete job"
                                            className="rounded-md p-2 text-slate-400 transition hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-900/30 dark:hover:text-rose-400"
                                        >
                                            <Trash2 className="h-4 w-4" />
                                        </button>
                                    </div>
                                </li>
                            );
                        })}
                    </ul>
                )}
            </section>

            <ConfirmModal
                open={Boolean(deleteTarget)}
                title="Delete job"
                message="Remove this job and its Excel files. If a laptop is running it, the laptop stops that bot on its next check."
                confirmLabel="Delete"
                loading={deleting}
                onClose={() => setDeleteTarget(null)}
                onConfirm={onDelete}
            />
        </div>
    );
}
