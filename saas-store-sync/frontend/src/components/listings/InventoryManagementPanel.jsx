import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, FileDown, Pencil, Play, RefreshCw, Send, Trash2 } from 'lucide-react';
import Button from '../ui/Button';
import {
    criticalZeroListingInventory,
    deleteListing,
    exportListingInventory,
    getListings,
    pushListingInventory,
    resetListingInventory,
    scrapeListings,
} from '../../services/listingService';
import ListingFormModal from './ListingFormModal';

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

function JobProgressStrip({ mode, count }) {
    if (!mode) return null;
    const isScrape = mode === 'scrape';
    const title = isScrape ? 'Fetching vendor prices…' : 'Pushing price/stock to marketplace…';
    const detail = isScrape
        ? `Scraping ${count || 0} listing(s) with vendor links. Keep this page open until it finishes.`
        : `Updating ${count || 0} listing(s) on Reverb. Keep this page open until it finishes.`;
    const border = isScrape
        ? 'border-sky-200 dark:border-sky-800 bg-sky-50/80 dark:bg-sky-950/30'
        : 'border-emerald-200 dark:border-emerald-800 bg-emerald-50/80 dark:bg-emerald-950/30';
    const bar = isScrape ? 'bg-sky-500' : 'bg-emerald-500';
    const pill = isScrape
        ? 'bg-sky-200 text-sky-900 dark:bg-sky-800 dark:text-sky-200'
        : 'bg-emerald-200 text-emerald-900 dark:bg-emerald-800 dark:text-emerald-200';

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
                    </h3>
                    <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-300">{detail}</p>
                    <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                        <div
                            className={`h-full w-1/3 rounded-full ${bar}`}
                            style={{
                                animation: 'managedInvProgress 1.2s ease-in-out infinite',
                            }}
                        />
                    </div>
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

/** Managed-store inventory: same scrape/push/reset/export ideas as Product listings. */
export default function InventoryManagementPanel({ storeId, marketplaceCode = '', reloadNonce = 0, onMessage }) {
    const isReverb = String(marketplaceCode || '').trim().toLowerCase() === 'reverb';
    const [listings, setListings] = useState([]);
    const [loading, setLoading] = useState(false);
    const [scraping, setScraping] = useState(false);
    const [pushing, setPushing] = useState(false);
    const [resetting, setResetting] = useState(false);
    const [criticalLoading, setCriticalLoading] = useState(false);
    const [exporting, setExporting] = useState(false);
    const [editListing, setEditListing] = useState(null);
    const [editOpen, setEditOpen] = useState(false);
    const [search, setSearch] = useState('');
    const [syncFilter, setSyncFilter] = useState('all');
    const [jobCount, setJobCount] = useState(0);

    const load = useCallback(() => {
        if (!storeId) return;
        setLoading(true);
        const params = { view: 'inventory', search: search || undefined };
        if (syncFilter !== 'all') params.sync_status = syncFilter;
        getListings(storeId, params)
            .then((res) => setListings(Array.isArray(res.data) ? res.data : []))
            .catch(() => onMessage?.('Failed to load inventory.', 'error'))
            .finally(() => setLoading(false));
    }, [storeId, search, syncFilter, onMessage]);

    useEffect(() => {
        load();
    }, [load, reloadNonce]);

    const withVendor = useMemo(
        () => listings.filter((l) => (l.vendor_url || '').trim()).length,
        [listings],
    );

    const handleScrape = (ids = null) => {
        const targetCount = ids?.length
            ? ids.length
            : listings.filter((l) => (l.vendor_url || '').trim()).length;
        setJobCount(targetCount);
        setScraping(true);
        scrapeListings(storeId, ids)
            .then((res) => {
                onMessage?.(res.data?.message || 'Scrape finished.', res.data?.ok ? 'success' : 'error');
                load();
            })
            .catch((err) => {
                onMessage?.(err.response?.data?.detail || 'Scrape failed.', 'error');
                load();
            })
            .finally(() => {
                setScraping(false);
                setJobCount(0);
            });
    };

    const handlePush = (ids = null) => {
        const targetCount = ids?.length || listings.length;
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
        if (!window.confirm('Set stock to 0 on all marketplace listings and push to Reverb?')) return;
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

    const busy = scraping || pushing || resetting || criticalLoading;

    return (
        <div className="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900">
            {(scraping || pushing) && (
                <div className="border-b border-slate-200 dark:border-slate-700 p-4">
                    <JobProgressStrip
                        mode={scraping ? 'scrape' : 'push'}
                        count={jobCount}
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
                                : `${listings.length.toLocaleString()} listing${listings.length === 1 ? '' : 's'} on the marketplace`}
                        </p>
                    </div>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                    <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => handlePush()}
                        disabled={busy || listings.length === 0}
                        title="Push current price/stock to marketplace (no new vendor fetch)"
                    >
                        <RefreshCw className={`mr-1.5 h-4 w-4 ${pushing ? 'animate-spin' : ''}`} />
                        Manual sync
                    </Button>

                    <div className="relative">
                        <Button
                            variant="secondary"
                            size="sm"
                            disabled={busy || listings.length === 0}
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

                    {isReverb && (
                        <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => handleScrape()}
                            disabled={busy || withVendor === 0}
                            title={withVendor === 0 ? 'Add Vendor URL on each listing first' : 'Refresh vendor price and stock'}
                        >
                            <Play className={`mr-1.5 h-4 w-4 ${scraping ? 'animate-spin' : ''}`} />
                            {scraping ? 'Scraping…' : `Start Scraping (${withVendor})`}
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
                                            {isReverb && (
                                                <button
                                                    type="button"
                                                    title="Scrape vendor URL"
                                                    className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800 disabled:opacity-40"
                                                    onClick={() => handleScrape([l.id])}
                                                    disabled={busy || !(l.vendor_url || '').trim()}
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
