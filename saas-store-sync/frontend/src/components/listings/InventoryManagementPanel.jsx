import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, FileDown, Pencil, Play, RefreshCw, Send, Trash2 } from 'lucide-react';
import Button from '../ui/Button';
import {
    criticalZeroListingInventory,
    deleteListing,
    exportListingInventory,
    getInventoryListings,
    getListingScrapeProgress,
    pushListingInventory,
    resetListingInventory,
    scrapeListings,
} from '../../services/listingService';
import ListingFormModal from './ListingFormModal';

const PAGE_SIZE = 10;

const SYNC_STYLES = {
    pending: 'bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300',
    scraped: 'bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300',
    synced: 'bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300',
    failed: 'bg-rose-50 dark:bg-rose-900/30 text-rose-700 dark:text-rose-300',
};

const SYNC_LABELS = {
    pending: 'Pending',
    scraped: 'Scraped',
    synced: 'Synced',
    failed: 'Failed',
};

function formatAgo(iso) {
    if (!iso) return '—';
    const t = new Date(iso).getTime();
    if (!Number.isFinite(t)) return '—';
    const sec = Math.round((Date.now() - t) / 1000);
    if (sec < 60) return `${sec}s ago`;
    if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
    if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
    return `${Math.round(sec / 86400)}d ago`;
}

/** Prefer API margin_display; else sale ÷ vendor as ×N (2× cost → ×2). */
function formatListingMargin(listing) {
    if (listing.margin_display) return listing.margin_display;
    const v = parseFloat(listing.vendor_price);
    const s = parseFloat(listing.sale_price);
    if (!Number.isFinite(v) || !Number.isFinite(s) || v <= 0 || s <= 0) return '—';
    return `×${Math.round((s / v) * 100) / 100}`;
}

/**
 * Progress strip matching catalog Manual sync / scrape UX:
 * always show processed/total counts while scraping.
 */
function JobProgressStrip({ mode, count, progress, marketplaceLabel = 'marketplace' }) {
    if (!mode) return null;
    const isScrape = mode === 'scrape';
    const processed = Number(progress?.processed ?? 0);
    const total = Math.max(
        Number(progress?.total ?? 0) || 0,
        Number(count ?? 0) || 0,
    );
    const scraped = Number(progress?.scraped ?? 0);
    const failed = Number(progress?.failed ?? 0);
    const pct = total > 0
        ? Math.max(0, Math.min(100, Number(progress?.pct ?? Math.round((100 * processed) / total))))
        : 0;
    const hasCounts = isScrape && total > 0;
    const phase = progress?.phase || '';
    const title = isScrape
        ? (phase === 'pushing' ? 'Pushing scraped prices…' : 'Fetching vendor prices…')
        : `Pushing price/stock to ${marketplaceLabel}…`;
    const detail = isScrape
        ? (hasCounts
            ? (
                `Scraping ${Math.min(Math.max(processed, processed < total ? processed + (phase === 'running' ? 1 : 0) : processed), total).toLocaleString()} of ${total.toLocaleString()}`
                + ` · ${processed.toLocaleString()}/${total.toLocaleString()} done`
                + ` · ${scraped.toLocaleString()} ok`
                + ` · ${failed.toLocaleString()} failed`
                + `. Progress follows listing status (Pending → Scraped).`
            )
            : 'Starting scrape…')
        : `Updating ${count || 0} listing(s) on ${marketplaceLabel}. Keep this page open until it finishes.`;
    const border = isScrape
        ? 'border-sky-200 dark:border-sky-800 bg-sky-50/80 dark:bg-sky-950/30'
        : 'border-emerald-200 dark:border-emerald-800 bg-emerald-50/80 dark:bg-emerald-950/30';
    const bar = isScrape ? 'bg-sky-500' : 'bg-emerald-500';
    const pill = isScrape
        ? 'bg-sky-200 text-sky-900 dark:bg-sky-800 dark:text-sky-200'
        : 'bg-emerald-200 text-emerald-900 dark:bg-emerald-800 dark:text-emerald-200';
    const sku = (progress?.current_sku || '').trim();

    return (
        <div className={`rounded-lg border ${border} p-4 mb-0 shadow-sm`}>
            <div className="flex items-start gap-3">
                <span className={`mt-0.5 inline-flex h-2.5 w-2.5 shrink-0 rounded-full ${bar} animate-pulse`} />
                <div className="min-w-0 flex-1">
                    <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                        {title}
                        <span className={`ml-2 inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${pill}`}>
                            running
                        </span>
                        {hasCounts ? (
                            <span className="ml-2 text-xs font-semibold tabular-nums text-slate-600 dark:text-slate-300">
                                {processed}/{total}
                            </span>
                        ) : null}
                    </h3>
                    <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-300">{detail}</p>
                    {sku ? (
                        <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400 truncate" title={sku}>
                            Current: {sku}
                        </p>
                    ) : null}
                    <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                        {hasCounts ? (
                            <div
                                className={`h-full rounded-full transition-all duration-500 ${bar}`}
                                style={{ width: `${Math.max(pct, total > 0 ? 2 : 0)}%` }}
                            />
                        ) : (
                            <div
                                className={`h-full w-1/3 rounded-full ${bar}`}
                                style={{ animation: 'managedInvProgress 1.2s ease-in-out infinite' }}
                            />
                        )}
                    </div>
                    {hasCounts ? (
                        <p className="mt-1 text-xs tabular-nums text-slate-500 dark:text-slate-400">
                            {pct}% complete ({processed} of {total})
                        </p>
                    ) : null}
                </div>
            </div>
            <style>{`
                @keyframes managedInvProgress {
                    0% { transform: translateX(-100%); }
                    100% { transform: translateX(400%); }
                }
            `}</style>
        </div>
    );
}

function inventoryLoadError(err) {
    if (!err) return 'Failed to load inventory.';
    if (err.code === 'ERR_CANCELED' || err.name === 'CanceledError' || err.name === 'AbortError') {
        return null;
    }
    if (err.response?.status === 429) {
        return 'Too many requests — wait a moment, then refresh. Your inventory list is unchanged.';
    }
    const detail = err.response?.data?.detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (err.code === 'ECONNABORTED') return 'Inventory request timed out. Try again.';
    if (!err.response) return 'Could not reach the server. Check your connection and try again.';
    return err.message || 'Failed to load inventory.';
}

/** Managed-store inventory: scrape then Manual sync / schedule — same as Reverb. */
export default function InventoryManagementPanel({ storeId, marketplaceCode = '', reloadNonce = 0, onMessage }) {
    const marketplaceLabel = String(marketplaceCode || '').trim() || 'marketplace';
    const isReverb = marketplaceLabel.toLowerCase() === 'reverb';
    const isLasoo = marketplaceLabel.toLowerCase() === 'lasoo';
    const isEtsy = marketplaceLabel.toLowerCase() === 'etsy';
    const canScrape = isReverb || isLasoo || isEtsy;
    const [listings, setListings] = useState([]);
    const [totalCount, setTotalCount] = useState(0);
    const [scrapeableCount, setScrapeableCount] = useState(0);
    const [page, setPage] = useState(1);
    const [loading, setLoading] = useState(false);
    const [scraping, setScraping] = useState(false);
    const [pushing, setPushing] = useState(false);
    const [resetting, setResetting] = useState(false);
    const [criticalLoading, setCriticalLoading] = useState(false);
    const [exporting, setExporting] = useState(false);
    const [editListing, setEditListing] = useState(null);
    const [editOpen, setEditOpen] = useState(false);
    const [search, setSearch] = useState('');
    const [debouncedSearch, setDebouncedSearch] = useState('');
    const [syncFilter, setSyncFilter] = useState('all');
    const [jobCount, setJobCount] = useState(0);
    const [scrapeProgress, setScrapeProgress] = useState(null);
    const scrapeWasActiveRef = useRef(false);
    const onMessageRef = useRef(onMessage);
    const loadGenRef = useRef(0);
    const scrapeListRefreshTickRef = useRef(0);
    const pageRef = useRef(1);

    useEffect(() => {
        onMessageRef.current = onMessage;
    }, [onMessage]);

    useEffect(() => {
        pageRef.current = page;
    }, [page]);

    // Debounce search so typing does not fire a request per keystroke.
    useEffect(() => {
        const t = setTimeout(() => setDebouncedSearch(search.trim()), 300);
        return () => clearTimeout(t);
    }, [search]);

    // Reset to first page when filters change.
    useEffect(() => {
        setPage(1);
    }, [debouncedSearch, syncFilter, storeId]);

    const applyInventoryPage = useCallback((res) => {
        setListings(Array.isArray(res.data) ? res.data : []);
        setTotalCount(Number.isFinite(res.count) ? res.count : 0);
        if (Number.isFinite(res.scrapeableCount)) {
            setScrapeableCount(res.scrapeableCount);
        }
    }, []);

    const load = useCallback(() => {
        if (!storeId) return;
        const gen = ++loadGenRef.current;
        setLoading(true);
        getInventoryListings(storeId, {
            page,
            pageSize: PAGE_SIZE,
            search: debouncedSearch || undefined,
            syncStatus: syncFilter,
        })
            .then((res) => {
                if (gen !== loadGenRef.current) return;
                applyInventoryPage(res);
                // If filters shrunk the result set past the current page, clamp.
                if (res.page > res.totalPages && res.totalPages >= 1) {
                    setPage(res.totalPages);
                }
            })
            .catch((err) => {
                if (gen !== loadGenRef.current) return;
                // Page out of range after deletes/filters — jump back to page 1.
                if (err.response?.status === 404 && page > 1) {
                    setPage(1);
                    return;
                }
                // Keep existing rows — clearing looks like inventory disappeared.
                const msg = inventoryLoadError(err);
                if (msg) onMessageRef.current?.(msg, 'error');
            })
            .finally(() => {
                if (gen !== loadGenRef.current) return;
                setLoading(false);
            });
    }, [storeId, page, debouncedSearch, syncFilter, applyInventoryPage]);

    useEffect(() => {
        load();
    }, [load, reloadNonce]);

    // Always poll server scrape progress so the bar survives page reload
    // (same idea as catalog Inventory management Celery scrape strip).
    useEffect(() => {
        if (!storeId) return undefined;
        let cancelled = false;
        scrapeListRefreshTickRef.current = 0;
        const tick = () => {
            getListingScrapeProgress(storeId)
                .then((res) => {
                    if (cancelled) return;
                    const data = res.data || null;
                    const active = Boolean(data?.active);
                    const wasActive = scrapeWasActiveRef.current;
                    if (wasActive && !active) {
                        const msg = data?.message || 'Scrape finished. Use Manual sync to push to the marketplace.';
                        const ok = (data?.scraped || 0) > 0 || data?.phase === 'done';
                        onMessageRef.current?.(msg, ok ? 'success' : 'error');
                        setScraping(false);
                        setJobCount(0);
                        load();
                    }
                    scrapeWasActiveRef.current = active;
                    setScrapeProgress(data);
                    if (active) {
                        setScraping(true);
                        setJobCount(Number(data?.total || 0));
                        // Refresh rows less often than progress (~every 4.5s) to avoid throttle spam.
                        scrapeListRefreshTickRef.current += 1;
                        if (scrapeListRefreshTickRef.current % 3 !== 0) return;
                        getInventoryListings(storeId, {
                            page: pageRef.current,
                            pageSize: PAGE_SIZE,
                            search: debouncedSearch || undefined,
                            syncStatus: syncFilter,
                        })
                            .then((r) => {
                                if (!cancelled) applyInventoryPage(r);
                            })
                            .catch(() => {});
                    } else {
                        scrapeListRefreshTickRef.current = 0;
                    }
                })
                .catch(() => { /* ignore transient poll errors */ });
        };
        tick();
        const id = setInterval(tick, 1500);
        return () => {
            cancelled = true;
            clearInterval(id);
        };
    }, [storeId, debouncedSearch, syncFilter, load, applyInventoryPage]);

    const withVendor = scrapeableCount;

    const serverScraping = Boolean(scrapeProgress?.active);
    const scrapeBusy = scraping || serverScraping;

    const handleScrape = (ids = null) => {
        const targetCount = ids?.length ? ids.length : withVendor;
        setJobCount(targetCount);
        setScrapeProgress({
            active: true,
            total: targetCount,
            processed: 0,
            scraped: 0,
            failed: 0,
            pct: 0,
            phase: 'queued',
            message: 'Starting scrape…',
        });
        scrapeWasActiveRef.current = true;
        setScraping(true);
        scrapeListings(storeId, ids)
            .then((res) => {
                const serverTotal = Number(res.data?.total || 0);
                if (serverTotal > 0) {
                    setJobCount(serverTotal);
                    setScrapeProgress((prev) => ({
                        ...(prev || {}),
                        active: true,
                        total: serverTotal,
                        phase: prev?.phase || 'queued',
                        message: res.data?.message || prev?.message,
                    }));
                }
                if (res.data?.already_running) {
                    onMessage?.(res.data?.message || 'Scrape already running.', 'success');
                } else {
                    onMessage?.(res.data?.message || 'Scrape started. You can reload — progress continues.', 'success');
                }
                // Progress + completion are driven by the server poll above.
            })
            .catch((err) => {
                scrapeWasActiveRef.current = false;
                setScraping(false);
                setScrapeProgress(null);
                onMessage?.(err.response?.data?.detail || 'Scrape failed.', 'error');
                load();
            });
    };

    const handlePush = (ids = null) => {
        const targetCount = ids?.length || totalCount;
        setJobCount(targetCount);
        setPushing(true);
        pushListingInventory(storeId, ids)
            .then((res) => {
                onMessage?.(res.data?.message || 'Manual sync finished.', res.data?.ok ? 'success' : 'error');
                load();
            })
            .catch((err) => {
                onMessage?.(err.response?.data?.detail || err.response?.data?.message || 'Push failed.', 'error');
                load();
            })
            .finally(() => {
                setPushing(false);
                setJobCount(0);
            });
    };

    const handleReset = (scope) => {
        setResetting(true);
        resetListingInventory(storeId, scope)
            .then((res) => {
                onMessage?.(res.data?.message || 'Status reset.', 'success');
                load();
            })
            .catch((err) => onMessage?.(err.response?.data?.detail || 'Reset failed.', 'error'))
            .finally(() => setResetting(false));
    };

    const handleCriticalZero = () => {
        const label = marketplaceLabel.charAt(0).toUpperCase() + marketplaceLabel.slice(1);
        if (!window.confirm(`Set stock to 0 on all marketplace listings and push to ${label}?`)) return;
        setCriticalLoading(true);
        criticalZeroListingInventory(storeId)
            .then((res) => {
                onMessage?.(res.data?.message || 'Critical action finished.', res.data?.ok ? 'success' : 'error');
                load();
            })
            .catch((err) => onMessage?.(err.response?.data?.detail || 'Critical action failed.', 'error'))
            .finally(() => setCriticalLoading(false));
    };

    const handleExport = () => {
        setExporting(true);
        const status = syncFilter === 'all' ? '' : syncFilter;
        exportListingInventory(storeId, status)
            .then(() => onMessage?.('Inventory exported.', 'success'))
            .catch(() => onMessage?.('Export failed.', 'error'))
            .finally(() => setExporting(false));
    };

    const handleDelete = (listing) => {
        if (!window.confirm(`Remove "${listing.sku || listing.external_variant_key}" from the marketplace?`)) return;
        deleteListing(storeId, listing.id)
            .then(() => {
                onMessage?.(`Removed "${listing.sku || listing.external_variant_key}".`, 'success');
                load();
            })
            .catch((err) => onMessage?.(err.response?.data?.detail || 'Delete failed.', 'error'));
    };

    const busy = scrapeBusy || pushing || resetting || criticalLoading;
    const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE) || 1);
    const safePage = Math.min(Math.max(1, page), totalPages);
    const rangeStart = totalCount === 0 ? 0 : (safePage - 1) * PAGE_SIZE + 1;
    const rangeEnd = Math.min(safePage * PAGE_SIZE, totalCount);

    return (
        <div className="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900">
            {(scrapeBusy || pushing) && (
                <div className="border-b border-slate-200 dark:border-slate-700 p-4">
                    <JobProgressStrip
                        mode={scrapeBusy ? 'scrape' : 'push'}
                        count={jobCount}
                        progress={scrapeBusy ? scrapeProgress : null}
                        marketplaceLabel={marketplaceLabel}
                    />
                </div>
            )}
            <div className="flex flex-col gap-3 border-b border-slate-200 dark:border-slate-700 p-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                        <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Inventory management</h2>
                        <p className="text-xs text-slate-500 dark:text-slate-400">
                            {loading && listings.length === 0
                                ? 'Loading…'
                                : `${totalCount.toLocaleString()} listing${totalCount === 1 ? '' : 's'} on the marketplace`}
                            {canScrape ? (
                                <span className="block mt-0.5">
                                    Start Scraping updates vendor price/stock locally (Scraped). Manual sync or your store schedule pushes to {marketplaceLabel}.
                                </span>
                            ) : null}
                        </p>
                    </div>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                    <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => handlePush()}
                        disabled={busy || totalCount === 0}
                        title="Push current price/stock to marketplace (no new vendor fetch)"
                    >
                        <RefreshCw className={`mr-1.5 h-4 w-4 ${pushing ? 'animate-spin' : ''}`} />
                        Manual sync
                    </Button>

                    <div className="relative">
                        <Button
                            variant="secondary"
                            size="sm"
                            disabled={busy || totalCount === 0}
                            className="border-rose-300 text-rose-700 dark:border-rose-700 dark:text-rose-300"
                            onClick={handleCriticalZero}
                        >
                            <AlertTriangle className="mr-1.5 h-4 w-4" />
                            {criticalLoading ? 'Working…' : 'Critical action'}
                        </Button>
                    </div>

                    <select
                        className="rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-2 text-xs text-slate-900 dark:text-slate-100"
                        disabled={busy}
                        defaultValue=""
                        onChange={(e) => {
                            const v = e.target.value;
                            e.target.value = '';
                            if (v) handleReset(v);
                        }}
                        title="Reset scrape status so you can Start Scraping again"
                    >
                        <option value="" disabled>
                            {resetting ? 'Resetting…' : 'Reset status'}
                        </option>
                        <option value="failed">Reset failed → Pending</option>
                        <option value="scraped">Reset scraped → Pending</option>
                        <option value="all">Reset all → Pending</option>
                    </select>

                    <Button variant="secondary" size="sm" onClick={handleExport} disabled={exporting || !storeId}>
                        <FileDown className="mr-1.5 h-4 w-4" />
                        {exporting ? 'Exporting…' : 'Export'}
                    </Button>

                    {canScrape && (
                        <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => handleScrape()}
                            disabled={busy || withVendor === 0}
                            title={withVendor === 0 ? 'Add Vendor URL / Vendor ID on each listing first' : 'Refresh vendor price and stock locally (then Manual sync)'}
                        >
                            <Play className={`mr-1.5 h-4 w-4 ${scraping ? 'animate-spin' : ''}`} />
                            {scraping || serverScraping ? 'Scraping…' : `Start Scraping (${withVendor})`}
                        </Button>
                    )}

                    <input
                        type="search"
                        placeholder="Search SKU, title, vendor…"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="min-w-[12rem] flex-1 rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-1.5 text-sm text-slate-900 dark:text-slate-100"
                    />
                    <select
                        className="rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-2 text-xs text-slate-900 dark:text-slate-100"
                        value={syncFilter}
                        onChange={(e) => setSyncFilter(e.target.value)}
                    >
                        <option value="all">All statuses</option>
                        <option value="pending">Pending</option>
                        <option value="scraped">Scraped</option>
                        <option value="synced">Synced</option>
                        <option value="failed">Failed</option>
                    </select>
                    <Button variant="secondary" size="sm" onClick={load} disabled={loading}>
                        <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
                    </Button>
                </div>
            </div>

            <div className="overflow-x-auto">
                {loading && listings.length === 0 ? (
                    <p className="px-4 py-6 text-sm text-slate-500 dark:text-slate-400">Loading inventory…</p>
                ) : listings.length === 0 ? (
                    <p className="px-4 py-6 text-sm text-slate-500 dark:text-slate-400">
                        No listings on the marketplace yet. Create listings, publish from Created products, then manage inventory here.
                    </p>
                ) : (
                    <table className="w-full text-left text-sm">
                        <thead className="bg-slate-50 dark:bg-slate-800 text-xs uppercase text-slate-500 dark:text-slate-400">
                            <tr>
                                <th className="px-3 py-2.5">SKU</th>
                                <th className="px-3 py-2.5">Title</th>
                                <th className="px-3 py-2.5">Vendor URL</th>
                                <th className="px-3 py-2.5">Vendor ID</th>
                                <th className="px-3 py-2.5">Vendor price</th>
                                <th className="px-3 py-2.5">Price</th>
                                <th className="px-3 py-2.5">Stock</th>
                                <th className="px-3 py-2.5">Status</th>
                                <th className="px-3 py-2.5">Margin</th>
                                <th className="px-3 py-2.5">Last scrape</th>
                                <th className="px-3 py-2.5 text-right">Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {listings.map((l) => (
                                <tr key={l.id} className="border-t border-slate-100 dark:border-slate-800">
                                    <td className="px-3 py-2.5 font-medium text-slate-900 dark:text-slate-100 whitespace-nowrap">
                                        {l.sku || l.external_variant_key}
                                    </td>
                                    <td className="max-w-[180px] truncate px-3 py-2.5 text-slate-700 dark:text-slate-300" title={l.title}>
                                        {l.title || '—'}
                                    </td>
                                    <td className="px-3 py-2.5 text-xs">
                                        {l.vendor_url ? (
                                            <a
                                                href={l.vendor_url}
                                                target="_blank"
                                                rel="noreferrer"
                                                className="text-accent-600 dark:text-accent-400 hover:underline"
                                            >
                                                Link
                                            </a>
                                        ) : (
                                            <span className="text-amber-600 dark:text-amber-400">Missing</span>
                                        )}
                                    </td>
                                    <td className="px-3 py-2.5 text-xs text-slate-700 dark:text-slate-300 whitespace-nowrap">
                                        {l.vendor_id || <span className="text-slate-400">—</span>}
                                    </td>
                                    <td className="px-3 py-2.5 text-slate-700 dark:text-slate-300 whitespace-nowrap">
                                        {l.vendor_price != null ? `$${Number(l.vendor_price).toFixed(2)}` : '—'}
                                    </td>
                                    <td className="px-3 py-2.5 text-slate-700 dark:text-slate-300 whitespace-nowrap">
                                        ${Number(l.sale_price || 0).toFixed(2)}
                                    </td>
                                    <td className="px-3 py-2.5 text-slate-700 dark:text-slate-300">
                                        {l.infinite_quantity ? '∞' : l.inventory}
                                    </td>
                                    <td className="px-3 py-2.5">
                                        <span
                                            className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${SYNC_STYLES[l.inventory_sync_status] || SYNC_STYLES.pending}`}
                                            title={l.last_scrape_error || undefined}
                                        >
                                            {SYNC_LABELS[l.inventory_sync_status] || l.inventory_sync_status || 'Pending'}
                                        </span>
                                    </td>
                                    <td className="px-3 py-2.5 text-slate-700 dark:text-slate-300 whitespace-nowrap">
                                        {formatListingMargin(l)}
                                    </td>
                                    <td className="px-3 py-2.5 text-xs text-slate-500 dark:text-slate-400 whitespace-nowrap">
                                        {l.last_scrape_at ? `Scrape · ${formatAgo(l.last_scrape_at)}` : '—'}
                                    </td>
                                    <td className="px-3 py-2.5">
                                        <div className="flex items-center justify-end gap-1">
                                            {canScrape && (
                                                <button
                                                    type="button"
                                                    title="Scrape vendor URL / Nora stock"
                                                    className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800 disabled:opacity-40"
                                                    onClick={() => handleScrape([l.id])}
                                                    disabled={busy || (!(l.vendor_url || '').trim() && !(l.vendor_id || '').trim())}
                                                >
                                                    <Play className="h-4 w-4" />
                                                </button>
                                            )}
                                            <button
                                                type="button"
                                                title="Manual sync (push price/stock)"
                                                className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
                                                onClick={() => handlePush([l.id])}
                                                disabled={busy}
                                            >
                                                <Send className="h-4 w-4" />
                                            </button>
                                            <button
                                                type="button"
                                                title="Edit"
                                                className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
                                                onClick={() => { setEditListing(l); setEditOpen(true); }}
                                            >
                                                <Pencil className="h-4 w-4" />
                                            </button>
                                            <button
                                                type="button"
                                                title="Remove from marketplace"
                                                className="rounded-md p-1.5 text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-900/20"
                                                onClick={() => handleDelete(l)}
                                            >
                                                <Trash2 className="h-4 w-4" />
                                            </button>
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>

            {totalCount > PAGE_SIZE && (
                <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 dark:border-slate-700 px-4 py-3">
                    <p className="text-sm text-slate-500 dark:text-slate-400">
                        Showing{' '}
                        <span className="font-medium text-slate-700 dark:text-slate-300">{rangeStart}</span>
                        –
                        <span className="font-medium text-slate-700 dark:text-slate-300">{rangeEnd}</span>
                        {' '}of{' '}
                        <span className="font-medium text-slate-700 dark:text-slate-300">{totalCount.toLocaleString()}</span>
                        {' '}listings
                    </p>
                    <div className="flex items-center gap-1">
                        <button
                            type="button"
                            onClick={() => setPage((p) => Math.max(1, p - 1))}
                            disabled={safePage <= 1 || loading}
                            className="rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-1.5 text-sm font-medium text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700 transition disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                            Previous
                        </button>
                        {Array.from({ length: totalPages }, (_, i) => i + 1)
                            .filter((pg) => pg === 1 || pg === totalPages || Math.abs(pg - safePage) <= 1)
                            .reduce((acc, pg, idx, arr) => {
                                if (idx > 0 && pg - arr[idx - 1] > 1) acc.push('...');
                                acc.push(pg);
                                return acc;
                            }, [])
                            .map((pg, i) =>
                                pg === '...' ? (
                                    <span key={`dot-${i}`} className="px-1 text-slate-400">…</span>
                                ) : (
                                    <button
                                        key={pg}
                                        type="button"
                                        onClick={() => setPage(pg)}
                                        disabled={loading}
                                        className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
                                            pg === safePage
                                                ? 'bg-accent-600 text-white dark:bg-accent-500'
                                                : 'border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700'
                                        } disabled:opacity-40 disabled:cursor-not-allowed`}
                                    >
                                        {pg}
                                    </button>
                                )
                            )}
                        <button
                            type="button"
                            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                            disabled={safePage >= totalPages || loading}
                            className="rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-1.5 text-sm font-medium text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700 transition disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                            Next
                        </button>
                    </div>
                </div>
            )}

            <ListingFormModal
                open={editOpen}
                onClose={() => setEditOpen(false)}
                onSaved={() => load()}
                storeId={storeId}
                marketplaceCode={marketplaceCode}
                listing={editListing}
            />
        </div>
    );
}
