import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Download, Loader2, Play, ScanSearch, Table2, Trash2 } from 'lucide-react';
import Button from '../../components/ui/Button';
import ConfirmModal from '../../components/ui/ConfirmModal';
import Select from '../../components/ui/Select';
import PageHeader from '../../components/design/PageHeader';
import Badge from '../../components/design/Badge';
import EmptyState from '../../components/design/EmptyState';
import {
    continueDiscoveryJob,
    createDiscoveryJob,
    deleteDiscoveryJob,
    downloadDiscoveryIds,
    downloadDiscoveryResult,
    downloadDiscoveryTemplate,
    getDiscoveryJobs,
    getDiscoveryOptions,
} from '../../services/scrapingService';

const EMPTY_CLAUSE = { op: '', value: '', min: '', max: '' };

const ZIP_DEFAULTS = {
    amazon_us: '10001',
    amazon_au: '2000',
    ebay_us: '10001',
    ebay_au: '2000',
};

const STATUS_VARIANT = {
    queued: 'warning',
    running: 'accent',
    succeeded: 'success',
    failed: 'error',
};

function columnLabel(column) {
    return column.replace(/_/g, ' ');
}

function rulesPayload(form) {
    const clause = (raw) => {
        if (!raw.op) return { op: '' };
        if (raw.op === 'between') {
            return { op: 'between', min: raw.min === '' ? null : Number(raw.min), max: raw.max === '' ? null : Number(raw.max) };
        }
        return { op: raw.op, value: raw.value === '' ? null : Number(raw.value) };
    };
    const categories = form.excludeCategories
        .split(/[\n,;]+/)
        .map((part) => part.trim())
        .filter(Boolean);
    return {
        rating: clause(form.rating),
        reviews: clause(form.reviews),
        price: clause(form.price),
        exclude_categories: categories,
    };
}

function RuleRow({ label, hint, value, onChange }) {
    return (
        <div className="grid gap-2">
            <div>
                <p className="text-sm font-medium text-slate-800 dark:text-slate-100">{label}</p>
                <p className="text-xs text-slate-500 dark:text-slate-400">{hint}</p>
            </div>
            <Select
                label="Comparison"
                value={value.op}
                onChange={(e) => onChange({ ...value, op: e.target.value })}
                options={[
                    { value: '', label: 'Off' },
                    { value: 'lt', label: 'Remove if under' },
                    { value: 'gt', label: 'Remove if over' },
                    { value: 'between', label: 'Keep only between' },
                ]}
            />
            {value.op === 'between' ? (
                <div className="grid grid-cols-2 gap-2">
                    <label className="block text-sm text-slate-700 dark:text-slate-300">
                        Min
                        <input
                            type="number"
                            step="any"
                            value={value.min}
                            onChange={(e) => onChange({ ...value, min: e.target.value })}
                            className="mt-1 w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
                        />
                    </label>
                    <label className="block text-sm text-slate-700 dark:text-slate-300">
                        Max
                        <input
                            type="number"
                            step="any"
                            value={value.max}
                            onChange={(e) => onChange({ ...value, max: e.target.value })}
                            className="mt-1 w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
                        />
                    </label>
                </div>
            ) : (
                <label className="block text-sm text-slate-700 dark:text-slate-300">
                    Value
                    <input
                        type="number"
                        step="any"
                        disabled={!value.op}
                        value={value.value}
                        onChange={(e) => onChange({ ...value, value: e.target.value })}
                        className="mt-1 w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 disabled:opacity-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
                    />
                </label>
            )}
        </div>
    );
}

export default function Scraping() {
    const [options, setOptions] = useState(null);
    const [jobs, setJobs] = useState([]);
    const [loading, setLoading] = useState(true);
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState('');
    const [notice, setNotice] = useState('');
    const [file, setFile] = useState(null);
    const [fileKey, setFileKey] = useState(0);
    const [marketplace, setMarketplace] = useState('amazon_us');
    const [mode, setMode] = useState('category');
    const [zipCode, setZipCode] = useState(ZIP_DEFAULTS.amazon_us);
    const [useSample, setUseSample] = useState(false);
    const [columns, setColumns] = useState([]);
    const [rating, setRating] = useState({ ...EMPTY_CLAUSE });
    const [reviews, setReviews] = useState({ ...EMPTY_CLAUSE });
    const [price, setPrice] = useState({ ...EMPTY_CLAUSE });
    const [excludeCategories, setExcludeCategories] = useState('');
    const [deleteTarget, setDeleteTarget] = useState(null);
    const [deleting, setDeleting] = useState(false);

    const availableColumns = options?.columns?.[marketplace]?.[mode] || [];
    const defaultColumns = options?.defaults?.[marketplace]?.[mode] || [];

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const data = await getDiscoveryOptions();
                if (cancelled) return;
                setOptions(data);
                setColumns(data.defaults?.amazon_us?.category || []);
            } catch (err) {
                if (!cancelled) setError(err.response?.data?.detail || 'Could not load scraping options.');
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => {
            cancelled = true;
        };
    }, []);

    const refreshJobs = useCallback(async () => {
        const data = await getDiscoveryJobs();
        setJobs(data);
        return data;
    }, []);

    useEffect(() => {
        refreshJobs().catch(() => {});
    }, [refreshJobs]);

    const hasActiveJob = jobs.some((job) => job.status === 'queued' || job.status === 'running');
    useEffect(() => {
        if (!hasActiveJob) return undefined;
        const timer = setInterval(() => {
            refreshJobs().catch(() => {});
        }, 3000);
        return () => clearInterval(timer);
    }, [hasActiveJob, refreshJobs]);

    useEffect(() => {
        if (!options) return;
        setColumns(options.defaults?.[marketplace]?.[mode] || []);
    }, [marketplace, mode, options]);

    useEffect(() => {
        setZipCode(ZIP_DEFAULTS[marketplace] || '');
    }, [marketplace]);

    const marketplaceOptions = useMemo(
        () => (options?.marketplaces || []).map((item) => ({
            value: item.value,
            label: `${item.label} · ${item.region} server`,
        })),
        [options],
    );

    const toggleColumn = (column) => {
        setColumns((current) => (
            current.includes(column)
                ? current.filter((item) => item !== column)
                : [...current, column]
        ));
    };

    const formRules = () => rulesPayload({ rating, reviews, price, excludeCategories });

    const onSubmit = async (event) => {
        event.preventDefault();
        setError('');
        setNotice('');
        if (!file) {
            setError('Choose a CSV or XLSX file.');
            return;
        }
        if (!useSample && !/^[A-Za-z0-9]{3,10}$/.test(zipCode.trim())) {
            setError('Enter the delivery zip or postcode, such as 10001 or 2000.');
            return;
        }
        setSubmitting(true);
        try {
            await createDiscoveryJob({
                file,
                marketplace,
                mode,
                rules: formRules(),
                columns,
                useSample,
                zipCode: zipCode.trim(),
            });
            setFile(null);
            setFileKey((value) => value + 1);
            setNotice('Scrape queued. It runs on the US or AU worker, not on the main app worker.');
            await refreshJobs();
        } catch (err) {
            setError(err.response?.data?.detail || 'Could not start the scrape.');
        } finally {
            setSubmitting(false);
        }
    };

    const onDelete = async () => {
        if (!deleteTarget) return;
        setDeleting(true);
        setError('');
        try {
            await deleteDiscoveryJob(deleteTarget.id);
            setDeleteTarget(null);
            await refreshJobs();
        } catch (err) {
            setError(err.response?.data?.detail || 'Could not delete that scrape.');
        } finally {
            setDeleting(false);
        }
    };

    const onContinue = async (job) => {
        setError('');
        setNotice('');
        setSubmitting(true);
        try {
            await continueDiscoveryJob(job.id, {
                rules: formRules(),
                columns: options?.defaults?.[job.marketplace]?.product || [],
                useSample,
            });
            setNotice('Product details queued from the category results. Duplicates are removed again.');
            await refreshJobs();
        } catch (err) {
            setError(err.response?.data?.detail || 'Could not start product details.');
        } finally {
            setSubmitting(false);
        }
    };

    if (loading) {
        return <div className="p-8 text-slate-600 dark:text-slate-400">Loading scraping…</div>;
    }

    return (
        <div className="flex w-full min-w-0 flex-col gap-6">
            <PageHeader
                title="Scraping"
                description="Upload category or product links for Amazon and eBay. Rules and duplicate removal run before the result file is written. US and AU jobs use separate workers."
            />

            <form onSubmit={onSubmit} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-700 dark:bg-slate-900">
                <div className="grid gap-4 md:grid-cols-3">
                    <Select
                        label="Marketplace"
                        value={marketplace}
                        onChange={(e) => setMarketplace(e.target.value)}
                        options={marketplaceOptions}
                    />
                    <Select
                        label="What to scrape"
                        value={mode}
                        onChange={(e) => setMode(e.target.value)}
                        options={[
                            { value: 'category', label: 'Category' },
                            { value: 'product', label: 'Product details' },
                        ]}
                    />
                    <label className="block text-sm font-medium text-slate-700 dark:text-slate-300">
                        Delivery zip / postcode
                        <input
                            value={zipCode}
                            onChange={(e) => setZipCode(e.target.value)}
                            placeholder={marketplace.endsWith('_au') ? '2000' : '10001'}
                            maxLength={10}
                            className="mt-1 w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-normal text-slate-900 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
                        />
                    </label>
                </div>
                <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
                    Amazon uses this as the delivery location before it reads prices. eBay uses it as the buyer location.
                </p>

                <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center">
                    <label className="block flex-1 text-sm font-medium text-slate-700 dark:text-slate-300">
                        Upload file
                        <input
                            key={fileKey}
                            type="file"
                            accept=".csv,.xlsx,.xls"
                            onChange={(e) => setFile(e.target.files?.[0] || null)}
                            className="mt-1 block w-full text-sm text-slate-600 file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-2 file:text-sm file:font-medium file:text-slate-800 dark:text-slate-300 dark:file:bg-slate-800 dark:file:text-slate-100"
                        />
                    </label>
                    <Button
                        type="button"
                        variant="secondary"
                        onClick={() => downloadDiscoveryTemplate(marketplace, mode).catch((err) => {
                            setError(err.response?.data?.detail || 'Could not download the template.');
                        })}
                    >
                        <Download className="mr-2 h-4 w-4" />
                        Template
                    </Button>
                </div>
                <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
                    {mode === 'category'
                        ? 'Category file: one url column of search or category pages.'
                        : 'Product file: a url column, or asin / item_id. The same id is scraped once.'}
                    {file ? ` Selected ${file.name}.` : ''}
                </p>

                <label className="mt-4 flex items-start gap-2 text-sm text-slate-700 dark:text-slate-300">
                    <input
                        type="checkbox"
                        className="mt-1"
                        checked={useSample}
                        onChange={(e) => setUseSample(e.target.checked)}
                    />
                    <span>
                        Use local sample pages. Leave this off. When it is on, the file URLs are ignored and the table shows the fake list (B0SAMPLE01, Steel Cookware Set), not Amazon.
                    </span>
                </label>

                <div className="mt-6 space-y-4 border-t border-slate-100 pt-4 dark:border-slate-800">
                    <div>
                        <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Rules</h2>
                        <p className="text-xs text-slate-500 dark:text-slate-400">
                            Applied to category cards and to product details. A product must pass every rule. Duplicates are always removed.
                        </p>
                    </div>
                    <div className="grid gap-4 lg:grid-cols-3">
                        <RuleRow label="Rating" hint="Example: under 3.5" value={rating} onChange={setRating} />
                        <RuleRow label="Reviews" hint="Example: under 10" value={reviews} onChange={setReviews} />
                        <RuleRow label="Price" hint="Highest price is “over”" value={price} onChange={setPrice} />
                    </div>
                    <label className="block text-sm font-medium text-slate-800 dark:text-slate-100">
                        Remove these categories
                        <textarea
                            value={excludeCategories}
                            onChange={(e) => setExcludeCategories(e.target.value)}
                            rows={2}
                            placeholder="Books, Grocery"
                            className="mt-1 w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-normal text-slate-900 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
                        />
                    </label>
                </div>

                <div className="mt-6 border-t border-slate-100 pt-4 dark:border-slate-800">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Columns</h2>
                        <div className="flex gap-2">
                            <Button type="button" size="sm" variant="secondary" onClick={() => setColumns(defaultColumns)}>
                                Essentials
                            </Button>
                            <Button type="button" size="sm" variant="secondary" onClick={() => setColumns(availableColumns)}>
                                All columns
                            </Button>
                        </div>
                    </div>
                    <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                        The product id and URL are always in the file. Other columns are the ones you tick.
                    </p>
                    <div className="mt-3 flex flex-wrap gap-2">
                        {availableColumns.map((column) => {
                            const on = columns.includes(column);
                            return (
                                <button
                                    key={column}
                                    type="button"
                                    onClick={() => toggleColumn(column)}
                                    className={`rounded-full border px-3 py-1 text-xs font-medium ${
                                        on
                                            ? 'border-accent-500 bg-accent-50 text-accent-800 dark:bg-accent-900/40 dark:text-accent-200'
                                            : 'border-slate-200 text-slate-600 dark:border-slate-600 dark:text-slate-300'
                                    }`}
                                >
                                    {columnLabel(column)}
                                </button>
                            );
                        })}
                    </div>
                </div>

                {error && <p className="mt-4 text-sm text-rose-600 dark:text-rose-400">{error}</p>}
                {notice && <p className="mt-4 text-sm text-emerald-700 dark:text-emerald-400">{notice}</p>}

                <div className="mt-5">
                    <Button type="submit" disabled={submitting}>
                        {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Play className="mr-2 h-4 w-4" />}
                        Start scraping
                    </Button>
                </div>
            </form>

            <section className="rounded-xl border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900">
                <div className="border-b border-slate-100 px-5 py-4 dark:border-slate-800">
                    <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Jobs</h2>
                </div>
                {jobs.length === 0 ? (
                    <EmptyState
                        icon={ScanSearch}
                        title="No scrapes yet"
                        description="Upload a template, set rules, and start. The result file lists only the products that passed."
                    />
                ) : (
                    <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                        {jobs.map((job) => {
                            const stats = job.stats || {};
                            return (
                                <li key={job.id} className="flex flex-col gap-3 px-5 py-4">
                                    <div>
                                        <div className="flex flex-wrap items-center gap-2">
                                            <p className="text-sm font-medium text-slate-900 dark:text-slate-100">
                                                {job.marketplace_label} · {job.mode_label}
                                            </p>
                                            <Badge variant={STATUS_VARIANT[job.status] || 'default'}>{job.status}</Badge>
                                            {job.use_sample && <Badge>Sample pages</Badge>}
                                            {job.queue_name && <Badge>{job.queue_name}</Badge>}
                                        </div>
                                        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                                            {job.original_filename || 'Upload'}
                                            {job.zip_code ? ` · zip ${job.zip_code}` : ''}
                                            {stats.input_rows != null && ` · ${stats.input_rows} uploaded`}
                                            {stats.duplicates_removed != null && ` · ${stats.duplicates_removed} duplicates removed`}
                                            {stats.removed_by_rules != null && ` · ${stats.removed_by_rules} removed by rules`}
                                            {stats.kept != null && ` · ${stats.kept} in the file`}
                                        </p>
                                        {job.error_message && (
                                            <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">{job.error_message}</p>
                                        )}
                                        {stats.warning && (
                                            <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">{stats.warning}</p>
                                        )}
                                    </div>
                                    <div className="flex flex-nowrap items-center gap-2 overflow-x-auto pb-1">
                                        <Link
                                            to={`/scraping/${job.id}`}
                                            className="inline-flex shrink-0 items-center whitespace-nowrap rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-900 hover:bg-slate-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100 dark:hover:bg-slate-700"
                                        >
                                            <Table2 className="mr-2 h-4 w-4" />
                                            View products
                                        </Link>
                                        {job.status === 'succeeded' && (
                                            <Button
                                                type="button"
                                                size="sm"
                                                variant="secondary"
                                                className="shrink-0 whitespace-nowrap"
                                                onClick={() => downloadDiscoveryResult(job.id).catch(() => setError('Could not download the file.'))}
                                            >
                                                <Download className="mr-2 h-4 w-4" />
                                                Download
                                            </Button>
                                        )}
                                        {job.mode === 'category' && job.status === 'succeeded' && (
                                            <Button
                                                type="button"
                                                size="sm"
                                                variant="secondary"
                                                className="shrink-0 whitespace-nowrap"
                                                onClick={() => downloadDiscoveryIds(job.id).catch(() => setError('Could not download the product ids.'))}
                                            >
                                                <Download className="mr-2 h-4 w-4" />
                                                {String(job.marketplace || '').startsWith('ebay_') ? 'Download item ids' : 'Download ASINs'}
                                            </Button>
                                        )}
                                        {job.mode === 'category' && job.status === 'succeeded' && (
                                            <Button type="button" size="sm" className="shrink-0 whitespace-nowrap" disabled={submitting} onClick={() => onContinue(job)}>
                                                Product details
                                            </Button>
                                        )}
                                        <Button type="button" size="sm" variant="danger" className="shrink-0 whitespace-nowrap" onClick={() => setDeleteTarget(job)}>
                                            <Trash2 className="mr-2 h-4 w-4" />
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
                title="Delete scrape"
                message="Remove this job from the history and delete its result file."
                confirmLabel="Delete"
                loading={deleting}
                onClose={() => setDeleteTarget(null)}
                onConfirm={onDelete}
            />
        </div>
    );
}
