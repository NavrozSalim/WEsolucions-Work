import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, ChevronLeft, ChevronRight, Loader2, Search } from 'lucide-react';
import Badge from '../../components/design/Badge';
import PageHeader from '../../components/design/PageHeader';
import { getDiscoveryJob, getDiscoveryRows } from '../../services/scrapingService';

const STATUS_VARIANT = {
    queued: 'warning',
    running: 'accent',
    succeeded: 'success',
    failed: 'error',
};

function columnLabel(column) {
    return String(column || '').replace(/_/g, ' ');
}

const NUMERIC_COLUMNS = new Set(['price', 'rating', 'review_count']);

export default function ScrapingProducts() {
    const { jobId } = useParams();
    const [job, setJob] = useState(null);
    const [columns, setColumns] = useState([]);
    const [rows, setRows] = useState([]);
    const [total, setTotal] = useState(0);
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(10);
    const [query, setQuery] = useState('');
    const [search, setSearch] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);

    const searchRef = useRef(search);
    useEffect(() => {
        const timer = setTimeout(() => {
            const next = query.trim();
            if (searchRef.current === next) return;
            searchRef.current = next;
            setSearch(next);
            setPage(1);
        }, 300);
        return () => clearTimeout(timer);
    }, [query]);

    const load = useCallback(async () => {
        const [jobData, rowData] = await Promise.all([
            getDiscoveryJob(jobId),
            getDiscoveryRows(jobId, { q: search, page }),
        ]);
        setJob(jobData);
        setColumns(rowData.columns || []);
        setRows(rowData.rows || []);
        setTotal(rowData.total || 0);
        setPageSize(rowData.page_size || 10);
        if (rowData.page && rowData.page !== page) setPage(rowData.page);
        return jobData;
    }, [jobId, search, page]);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                await load();
            } catch (err) {
                if (!cancelled) setError(err.response?.data?.detail || 'Could not load products.');
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [load]);

    const live = job && (job.status === 'queued' || job.status === 'running');
    useEffect(() => {
        if (!live) return undefined;
        const timer = setInterval(() => {
            load().catch(() => {});
        }, 3000);
        return () => clearInterval(timer);
    }, [live, load]);

    if (loading) {
        return <div className="p-8 text-slate-600 dark:text-slate-400">Loading products…</div>;
    }

    return (
        <div className="flex w-full min-w-0 flex-col gap-4">
            <Link to="/scraping" className="inline-flex items-center text-sm text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100">
                <ArrowLeft className="mr-1 h-4 w-4" />
                Scraping
            </Link>
            <PageHeader
                title={job ? `${job.marketplace_label} · ${job.mode_label}` : 'Products'}
                description={
                    live
                        ? 'Products appear here as the scraper accepts them.'
                        : (job?.original_filename || 'Products kept by this scrape')
                }
                actions={job && (
                    <span className="inline-flex items-center gap-2">
                        {live && <Loader2 className="h-4 w-4 animate-spin text-slate-500" />}
                        <Badge variant={STATUS_VARIANT[job.status] || 'default'}>{job.status}</Badge>
                    </span>
                )}
            />
            {error && <p className="text-sm text-rose-600 dark:text-rose-400">{error}</p>}
            {job?.use_sample && (
                <p className="text-sm text-amber-700 dark:text-amber-300">
                    Sample list only. These are not products from your link.
                </p>
            )}
            {job?.stats?.warning && (
                <p className="text-sm text-amber-700 dark:text-amber-300">{job.stats.warning}</p>
            )}
            {job?.error_message && (
                <p className="text-sm text-rose-600 dark:text-rose-400">{job.error_message}</p>
            )}
            {job?.zip_code && (
                <p className="text-xs text-slate-500 dark:text-slate-400">Delivery zip {job.zip_code}</p>
            )}
            <div className="flex flex-wrap items-center gap-3">
                <label className="relative block w-full max-w-md">
                    <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                    <input
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder="Search title, id, or URL"
                        className="w-full rounded-md border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm text-slate-900 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
                    />
                </label>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                    {total} product{total === 1 ? '' : 's'}
                    {search ? ` matching “${search}”` : ''}
                    {live ? ' · updating' : ''}
                </p>
            </div>
            <div className="min-w-0 overflow-hidden rounded-xl border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900">
                {rows.length === 0 ? (
                    <p className="p-6 text-sm text-slate-500 dark:text-slate-400">
                        {live ? 'Waiting for the first product…' : 'No products in this scrape.'}
                    </p>
                ) : (
                    <div className="theme-scroll w-full overflow-x-auto">
                        <table className="w-max min-w-full text-left text-sm">
                            <thead className="bg-slate-50 text-slate-500 dark:bg-slate-800/80 dark:text-slate-400">
                                <tr>
                                    {columns.map((column) => (
                                        <th
                                            key={column}
                                            className={`whitespace-nowrap px-3 py-2.5 font-medium ${NUMERIC_COLUMNS.has(column) ? 'text-right' : 'text-left'}`}
                                        >
                                            {columnLabel(column)}
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map((row, index) => (
                                    <tr key={`${row.asin || row.item_id || row.url || index}`} className="border-t border-slate-100 dark:border-slate-800">
                                        {columns.map((column) => (
                                            <td
                                                key={column}
                                                className={`whitespace-nowrap px-3 py-2.5 text-slate-800 dark:text-slate-100 ${NUMERIC_COLUMNS.has(column) ? 'text-right tabular-nums' : ''}`}
                                            >
                                                {column === 'url' && row[column] ? (
                                                    <a href={row[column]} target="_blank" rel="noreferrer" className="text-accent-700 underline dark:text-accent-300">
                                                        {row[column]}
                                                    </a>
                                                ) : (
                                                    String(row[column] ?? '')
                                                )}
                                            </td>
                                        ))}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
                {total > pageSize && (
                    <div className="flex flex-wrap items-center justify-end gap-3 border-t border-slate-200 px-3 py-2.5 dark:border-slate-700">
                        <p className="text-xs text-slate-500 dark:text-slate-400">
                            Page {page} of {Math.max(1, Math.ceil(total / pageSize))}
                            {' · '}
                            {Math.min((page - 1) * pageSize + 1, total)}–{Math.min(page * pageSize, total)} of {total}
                        </p>
                        <div className="flex gap-2">
                            <button
                                type="button"
                                disabled={page <= 1}
                                onClick={() => setPage((current) => Math.max(1, current - 1))}
                                className="inline-flex items-center rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-900 disabled:opacity-40 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
                            >
                                <ChevronLeft className="mr-1 h-4 w-4" />
                                Previous
                            </button>
                            <button
                                type="button"
                                disabled={page >= Math.ceil(total / pageSize)}
                                onClick={() => setPage((current) => current + 1)}
                                className="inline-flex items-center rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-900 disabled:opacity-40 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
                            >
                                Next
                                <ChevronRight className="ml-1 h-4 w-4" />
                            </button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
