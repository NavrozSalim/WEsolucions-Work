import { useCallback, useEffect, useState } from 'react';
import { Bot, Download, Laptop, Loader2, Play, Square, Trash2 } from 'lucide-react';
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
                <div className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-900/30 dark:text-rose-300">
                    {error}
                </div>
            )}

            <form onSubmit={onSubmit} className="rounded-xl border border-slate-200 bg-white p-5 shadow-xs dark:border-slate-700 dark:bg-slate-900">
                <div className="grid gap-4 md:grid-cols-3">
                    <Select
                        label="Job"
                        value={jobType}
                        onChange={(e) => setJobType(e.target.value)}
                        options={jobTypes.length ? jobTypes : [{ value: jobType, label: jobType }]}
                    />
                    <label className="block text-sm font-medium text-slate-800 dark:text-slate-100">
                        Store
                        <span className="mt-0.5 block text-xs font-normal text-slate-500 dark:text-slate-400">
                            Optional. Empty splits the file by store_name: one job per store, each on that store&apos;s laptop.
                        </span>
                        <input
                            value={storeName}
                            onChange={(e) => setStoreName(e.target.value)}
                            placeholder="AMH"
                            className="mt-1 w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-normal text-slate-900 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
                        />
                    </label>
                    <label className="block text-sm font-medium text-slate-800 dark:text-slate-100">
                        Excel file
                        <input
                            key={fileKey}
                            type="file"
                            accept=".xlsx"
                            onChange={(e) => setFile(e.target.files?.[0] || null)}
                            className="mt-1 block w-full text-sm text-slate-600 file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-2 file:text-sm file:font-medium file:text-slate-800 dark:text-slate-300 dark:file:bg-slate-800 dark:file:text-slate-100"
                        />
                    </label>
                </div>
                <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">{hintFor(jobType)}</p>
                <div className="mt-4 flex justify-end">
                    <Button type="submit" disabled={submitting}>
                        {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Play className="mr-2 h-4 w-4" />}
                        Send to laptops
                    </Button>
                </div>
            </form>

            <section className="rounded-xl border border-slate-200 bg-white px-5 py-4 dark:border-slate-700 dark:bg-slate-900">
                <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Laptops</h2>
                <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                    Each laptop runs the Etsy and Reverb jobs for its own stores, oldest first, for every user.
                </p>
                {laptops.length === 0 ? (
                    <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                        No laptop is set up yet. Jobs wait here until one starts.
                    </p>
                ) : (
                    <div className="mt-2 flex flex-wrap gap-2">
                        {laptops.map((laptop) => {
                            const seen = laptop.last_used_at ? Date.parse(laptop.last_used_at) : 0;
                            const online = seen && now - seen < ONLINE_MS;
                            let state = 'offline';
                            if (online && laptop.busy) state = laptop.job_label ? `running your ${laptop.job_label}` : 'busy';
                            else if (online) state = 'free';
                            else if (seen) state = `offline, last seen ${formatTime(laptop.last_used_at)}`;
                            else state = 'never connected';
                            return (
                                <Badge
                                    key={laptop.label}
                                    variant={!online ? 'default' : laptop.busy ? 'warning' : 'success'}
                                    className="gap-1"
                                >
                                    <Laptop className="h-3 w-3" />
                                    {laptop.label}
                                    {laptop.stores?.length ? ` (${laptop.stores.join(', ')})` : ' (any store)'} · {state}
                                </Badge>
                            );
                        })}
                    </div>
                )}
            </section>

            <section className="rounded-xl border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900">
                <div className="border-b border-slate-100 px-5 py-4 dark:border-slate-800">
                    <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Jobs</h2>
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
                            return (
                                <li key={job.id} className="flex flex-col gap-3 px-5 py-4 md:flex-row md:items-center md:justify-between">
                                    <div className="min-w-0">
                                        <div className="flex flex-wrap items-center gap-2">
                                            <span className="text-sm font-medium text-slate-900 dark:text-slate-100">{job.job_label}</span>
                                            <Badge variant={STATUS_VARIANT[job.status] || 'default'}>
                                                {STATUS_LABEL[job.status] || job.status}
                                            </Badge>
                                            {job.status === 'pending' && !job.has_laptop && (
                                                <Badge variant="error">No laptop is set up for store {job.store_name}</Badge>
                                            )}
                                            {job.status === 'pending' && job.has_laptop && job.queue_position > 1 && (
                                                <Badge>{job.queue_position - 1} ahead for {job.store_name}</Badge>
                                            )}
                                            {job.store_name && <Badge>Store {job.store_name}</Badge>}
                                            {hasCounts && (
                                                <Badge variant={job.remaining_count ? 'warning' : 'success'}>
                                                    {job.done_count} finished / {job.remaining_count} remaining
                                                </Badge>
                                            )}
                                        </div>
                                        <p className="mt-1 truncate text-xs text-slate-500 dark:text-slate-400">
                                            {job.file_name} · sent {formatTime(job.requested_at)}
                                            {job.laptop ? ` · ${job.laptop}` : ''}
                                            {job.completed_at ? ` · ended ${formatTime(job.completed_at)}` : ''}
                                        </p>
                                        {job.note && (
                                            <p className="mt-1 whitespace-pre-line text-xs text-slate-500 dark:text-slate-400">{job.note}</p>
                                        )}
                                    </div>
                                    <div className="flex flex-wrap gap-2">
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
                                        <Button size="sm" variant="secondary" onClick={() => setDeleteTarget(job)}>
                                            <Trash2 className="mr-1 h-4 w-4" />
                                            Delete
                                        </Button>
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
