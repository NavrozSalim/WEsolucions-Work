import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, ChevronLeft, ChevronRight, Download, Loader2, Search, Square } from 'lucide-react';

import Badge from '../../components/design/Badge';
import PageHeader from '../../components/design/PageHeader';
import Button from '../../components/ui/Button';
import {
  cancelDiscoveryJob,
  downloadDiscoveryResult,
  getDiscoveryJob,
  getDiscoveryRows,
} from '../../services/scrapingService';

const STATUS_VARIANT = {
  queued: 'warning',
  running: 'accent',
  succeeded: 'success',
  failed: 'error',
  cancelled: 'default',
};

function columnLabel(column) {
  return String(column || '').replace(/_/g, ' ');
}

const NUMERIC_COLUMNS = new Set([
  'price',
  'rating',
  'review_count',
  'reviews',
  'inventory',
]);

const ID_COLUMNS = new Set(['asin', 'item_id']);

function getPageItems(currentPage, totalPages) {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, index) => index + 1);
  }

  const pages = new Set([
    1,
    totalPages,
    currentPage - 2,
    currentPage - 1,
    currentPage,
    currentPage + 1,
    currentPage + 2,
  ]);

  const validPages = [...pages]
    .filter((value) => value >= 1 && value <= totalPages)
    .sort((a, b) => a - b);

  const items = [];

  validPages.forEach((value, index) => {
    const previous = validPages[index - 1];
    if (index > 0 && value - previous > 1) {
      items.push(`ellipsis-${previous}-${value}`);
    }
    items.push(value);
  });

  return items;
}

function headerCellClass(column) {
  if (ID_COLUMNS.has(column)) return 'w-[150px] min-w-[150px] max-w-[180px]';
  if (column === 'url') return 'w-[82px] min-w-[82px] max-w-[82px]';
  if (column === 'title') return 'min-w-md';
  if (NUMERIC_COLUMNS.has(column)) return 'w-[110px] min-w-[100px]';
  if (column === 'availability') return 'w-[130px] min-w-[120px]';
  if (column === 'category') return 'w-[180px] min-w-[160px]';
  return 'min-w-[140px]';
}

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
  const [stopping, setStopping] = useState(false);

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

    if (rowData.page && rowData.page !== page) {
      setPage(rowData.page);
    }

    return jobData;
  }, [jobId, search, page]);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        await load();
      } catch (err) {
        if (!cancelled) {
          setError(err.response?.data?.detail || 'Could not load products.');
        }
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

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const firstRow = total === 0 ? 0 : Math.min((page - 1) * pageSize + 1, total);
  const lastRow = Math.min(page * pageSize, total);
  const pageItems = getPageItems(page, totalPages);

  const paginationButtonClass =
    'inline-flex h-9 min-w-9 items-center justify-center rounded-md border border-slate-200 bg-white px-2.5 text-sm font-medium text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800';

  return (
    <div className="flex w-full min-w-0 flex-col gap-4">
      <Link
        to="/scraping"
        className="inline-flex w-fit items-center text-sm text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"
      >
        <ArrowLeft className="mr-1 h-4 w-4" />
        Scraping
      </Link>

      <PageHeader
        title={job ? `${job.marketplace_label} · ${job.mode_label}` : 'Products'}
        description={
          live
            ? 'Products appear here as the scraper accepts them.'
            : job?.original_filename || 'Products kept by this scrape'
        }
        actions={
          job && (
            <span className="inline-flex flex-wrap items-center justify-end gap-2">
              {live && <Loader2 className="h-4 w-4 animate-spin text-slate-500" />}
              <Badge variant={STATUS_VARIANT[job.status] || 'default'}>{job.status}</Badge>
              {live && (
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  disabled={stopping}
                  onClick={() => {
                    setStopping(true);
                    cancelDiscoveryJob(jobId)
                      .then(() => load())
                      .catch((err) => setError(err.response?.data?.detail || 'Could not stop this scrape.'))
                      .finally(() => setStopping(false));
                  }}
                >
                  {stopping ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Square className="mr-2 h-4 w-4" />}
                  Stop
                </Button>
              )}
              {(total > 0 || job.status === 'succeeded' || job.status === 'cancelled') && (
                <>
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    onClick={() => downloadDiscoveryResult(jobId, 'xlsx').catch(() => setError('Could not download the Excel file.'))}
                  >
                    <Download className="mr-2 h-4 w-4" />
                    Excel
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    onClick={() => downloadDiscoveryResult(jobId, 'csv').catch(() => setError('Could not download the CSV file.'))}
                  >
                    <Download className="mr-2 h-4 w-4" />
                    CSV
                  </Button>
                </>
              )}
            </span>
          )
        }
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

      <div className="flex w-full items-center justify-between gap-4">
        <label className="relative block w-full max-w-md">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search title, id, or URL"
            className="w-full rounded-md border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm text-slate-900 outline-hidden transition focus:border-slate-300 focus:ring-2 focus:ring-slate-200/60 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:focus:border-slate-600 dark:focus:ring-slate-700/60"
          />
        </label>

        <p className="shrink-0 text-right text-xs text-slate-500 dark:text-slate-400 sm:text-sm">
          <span className="font-medium text-slate-700 dark:text-slate-200">
            {total.toLocaleString()} product{total === 1 ? '' : 's'}
          </span>
          {search ? ` matching “${search}”` : ''}
          {live ? ' · updating' : ''}
        </p>
      </div>

      <div className="min-w-0 overflow-hidden rounded-xl border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900">
        {rows.length === 0 ? (
          <div className="flex min-h-[220px] items-center justify-center p-6 text-center">
            <p className="text-sm text-slate-500 dark:text-slate-400">
              {live ? 'Waiting for the first product…' : 'No products in this scrape.'}
            </p>
          </div>
        ) : (
          <div className="theme-scroll max-h-[62vh] w-full overflow-x-auto overflow-y-auto">
            <table className="w-max min-w-full border-separate border-spacing-0 text-left text-sm">
              <thead className="text-slate-500 dark:text-slate-400">
                <tr>
                  {columns.map((column) => (
                    <th
                      key={column}
                      className={`sticky top-0 z-10 border-b border-slate-200 bg-slate-50/95 px-3 py-2.5 font-medium backdrop-blur-sm dark:border-slate-700 dark:bg-slate-800/95 ${headerCellClass(column)} ${NUMERIC_COLUMNS.has(column) ? 'text-right' : 'text-left'}`}
                    >
                      <span className="whitespace-nowrap">{columnLabel(column)}</span>
                    </th>
                  ))}
                </tr>
              </thead>

              <tbody>
                {rows.map((row, index) => (
                  <tr
                    key={`${row.asin || row.item_id || row.url || index}`}
                    className="transition-colors hover:bg-slate-50/70 dark:hover:bg-slate-800/40"
                  >
                    {columns.map((column) => {
                      const value = row[column];

                      if (column === 'url') {
                        return (
                          <td
                            key={column}
                            className="w-[82px] min-w-[82px] max-w-[82px] border-b border-slate-100 px-3 py-2.5 dark:border-slate-800"
                          >
                            {value ? (
                              <a
                                href={value}
                                target="_blank"
                                rel="noreferrer"
                                title={String(value)}
                                className="whitespace-nowrap font-medium text-accent-700 underline decoration-accent-300 underline-offset-2 hover:text-accent-800 dark:text-accent-300 dark:decoration-accent-700 dark:hover:text-accent-200"
                              >
                                Open
                              </a>
                            ) : (
                              <span className="text-slate-400">—</span>
                            )}
                          </td>
                        );
                      }

                      if (column === 'title') {
                        const title = String(value ?? '');
                        return (
                          <td
                            key={column}
                            title={title}
                            className="min-w-md max-w-xl whitespace-normal border-b border-slate-100 px-3 py-2.5 text-slate-800 dark:border-slate-800 dark:text-slate-100"
                          >
                            {title || '—'}
                          </td>
                        );
                      }

                      if (ID_COLUMNS.has(column)) {
                        return (
                          <td
                            key={column}
                            title={String(value ?? '')}
                            className="w-[150px] min-w-[150px] max-w-[180px] truncate whitespace-nowrap border-b border-slate-100 px-3 py-2.5 font-medium text-slate-800 dark:border-slate-800 dark:text-slate-100"
                          >
                            {String(value ?? '') || '—'}
                          </td>
                        );
                      }

                      if (NUMERIC_COLUMNS.has(column)) {
                        return (
                          <td
                            key={column}
                            className="w-[110px] min-w-[100px] whitespace-nowrap border-b border-slate-100 px-3 py-2.5 text-right tabular-nums text-slate-800 dark:border-slate-800 dark:text-slate-100"
                          >
                            {String(value ?? '') || '—'}
                          </td>
                        );
                      }

                      return (
                        <td
                          key={column}
                          title={String(value ?? '')}
                          className={`${headerCellClass(column)} whitespace-nowrap border-b border-slate-100 px-3 py-2.5 text-slate-800 dark:border-slate-800 dark:text-slate-100`}
                        >
                          {String(value ?? '') || '—'}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {total > pageSize && (
          <div className="flex flex-col gap-3 border-t border-slate-200 px-3 py-3 dark:border-slate-700 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-xs text-slate-500 dark:text-slate-400 sm:text-sm">
              {firstRow.toLocaleString()}–{lastRow.toLocaleString()} of {total.toLocaleString()}
            </p>

            <div className="theme-scroll flex items-center gap-1 overflow-x-auto pb-1 sm:pb-0">
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => setPage((current) => Math.max(1, current - 1))}
                className={`${paginationButtonClass} gap-1 px-3`}
              >
                <ChevronLeft className="h-4 w-4" />
                Previous
              </button>

              {pageItems.map((item) => {
                if (typeof item === 'string') {
                  return (
                    <span
                      key={item}
                      className="inline-flex h-9 min-w-7 items-center justify-center px-1 text-sm text-slate-400"
                    >
                      …
                    </span>
                  );
                }

                const active = item === page;

                return (
                  <button
                    key={item}
                    type="button"
                    onClick={() => setPage(item)}
                    aria-current={active ? 'page' : undefined}
                    className={`${paginationButtonClass} ${
                      active
                        ? 'border-accent-600 bg-accent-600 text-white hover:bg-accent-700 dark:border-accent-500 dark:bg-accent-500 dark:text-white dark:hover:bg-accent-400'
                        : ''
                    }`}
                  >
                    {item}
                  </button>
                );
              })}

              <button
                type="button"
                disabled={page >= totalPages}
                onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
                className={`${paginationButtonClass} gap-1 px-3`}
              >
                Next
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
