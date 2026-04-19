import { useState, useEffect, useRef, useCallback, useLayoutEffect } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import {
    Search,
    UploadCloud,
    RefreshCw,
    Trash2,
    ChevronLeft,
    Store,
    ExternalLink,
    Settings,
    Package,
    RotateCcw,
    FileText,
    Download,
    MoreVertical,
    AlertTriangle,
    FileDown,
    ScrollText,
} from 'lucide-react';
import { getMarketplaces, deleteStore } from '../../services/storeService';
import {
    getCatalogStores,
    getProducts,
    getCatalogUploads,
    uploadCatalog,
    triggerCatalogSync,
    triggerCatalogScrape,
    downloadSampleTemplate,
    resolveMarketplaceTemplateKind,
    resetProductSyncStatus,
    deleteCatalogUpload,
    downloadCatalogUploadErrors,
    downloadCatalogUploadFile,
    exportCatalogProducts,
    triggerCatalogPushListings,
    triggerCatalogCriticalZero,
    getCatalogActivityLogs,
    getScrapeProgress,
    cancelCatalogScrape,
} from '../../services/catalogService';
import Button from '../../components/ui/Button';
import Select from '../../components/ui/Select';
import ConfirmModal from '../../components/ui/ConfirmModal';
import PageHeader from '../../components/design/PageHeader';
import EmptyState from '../../components/design/EmptyState';
import Badge from '../../components/design/Badge';
import UpdateWithFileModal from '../../components/catalog/UpdateWithFileModal';
import { useSidebarActivity } from '../../context/SidebarActivityContext';

const syncStatusVariant = {
    synced: 'success',
    scraped: 'warning',
    needs_attention: 'error',
    pending: 'warning',
    failed: 'error',
};
const syncStatusLabel = {
    synced: 'Synced',
    scraped: 'Scrape',
    needs_attention: 'Needs attention',
    pending: 'Pending',
    failed: 'Failed',
};
const uploadStatusVariant = {
    synced: 'success', validated: 'success', pending: 'warning',
    processing: 'warning', partial: 'error', failed: 'error',
};
const uploadStatusLabel = {
    synced: 'Success', validated: 'Success', pending: 'Pending',
    processing: 'Processing', partial: 'Failed', failed: 'Failed',
};

function formatDate(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/** Short, user-friendly "~12 min" / "~45 sec" / "~1 h 5 min" from seconds. */
function formatEtaShort(seconds) {
    if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return null;
    const s = Math.round(seconds);
    if (s < 60) return `~${s} sec`;
    if (s < 3600) return `~${Math.round(s / 60)} min`;
    const h = Math.floor(s / 3600);
    const m = Math.round((s - h * 3600) / 60);
    return m === 0 ? `~${h} h` : `~${h} h ${m} min`;
}

/** Prefer server detail for 4xx/5xx so users see useful text instead of only "status code 500". */
function formatCatalogError(err) {
    const status = err.response?.status;
    const d = err.response?.data;
    if (typeof d === 'string' && d.trim()) return d;
    if (d?.detail != null) {
        if (typeof d.detail === 'string') return d.detail;
        if (Array.isArray(d.detail)) return d.detail.map((x) => x?.msg || x).filter(Boolean).join('; ') || `Request failed (${status}).`;
        return String(d.detail);
    }
    if (d?.error && typeof d.error === 'string') return d.error;
    if (d?.message && typeof d.message === 'string') return d.message;
    if (status === 500) return 'Something went wrong on the server (500). If this persists, try a smaller page size or contact support.';
    if (err.code === 'ECONNABORTED') return 'Request timed out. Check your connection and try again.';
    return err.message || `Request failed${status ? ` (${status})` : ''}.`;
}

function UploadActionsDropdown({ upload, storeId, syncing, scraping, syncingUploadId, scrapingUploadId, deletingUploadId, onSync, onScrape, onDelete, onDownload, onDownloadErrors, onError }) {
    const [open, setOpen] = useState(false);
    const [menuPos, setMenuPos] = useState({ top: 0, right: 0 });
    const triggerRef = useRef(null);
    const menuRef = useRef(null);

    const updatePosition = useCallback(() => {
        const el = triggerRef.current;
        if (!el) return;
        const r = el.getBoundingClientRect();
        setMenuPos({ top: r.bottom + 6, right: Math.max(8, window.innerWidth - r.right) });
    }, []);

    useLayoutEffect(() => {
        if (!open) return;
        updatePosition();
        const onScroll = () => updatePosition();
        window.addEventListener('scroll', onScroll, true);
        window.addEventListener('resize', onScroll);
        return () => {
            window.removeEventListener('scroll', onScroll, true);
            window.removeEventListener('resize', onScroll);
        };
    }, [open, updatePosition]);

    useEffect(() => {
        if (!open) return;
        const handler = (e) => {
            if (triggerRef.current?.contains(e.target) || menuRef.current?.contains(e.target)) return;
            setOpen(false);
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, [open]);

    const u = upload;
    const items = [];

    if (['pending', 'validated'].includes(u.status)) {
        items.push({
            label: 'Sync',
            icon: <RefreshCw className={`h-4 w-4 shrink-0 ${syncing && syncingUploadId === u.id ? 'animate-spin' : ''}`} />,
            onClick: () => onSync(u.id),
            disabled: syncing,
            className: 'text-accent-700 dark:text-accent-400',
        });
    }
    if (u.status === 'synced' || u.status === 'partial') {
        items.push({
            label: 'Scrape',
            icon: <RefreshCw className={`h-4 w-4 shrink-0 ${scraping && scrapingUploadId === u.id ? 'animate-spin' : ''}`} />,
            onClick: () => onScrape(u.id),
            disabled: scraping,
            className: 'text-slate-700 dark:text-slate-300',
        });
    }
    items.push({
        label: 'Download File',
        icon: <Download className="h-4 w-4 shrink-0" />,
        onClick: () => onDownload(storeId, u.id, u.original_filename),
        className: 'text-slate-700 dark:text-slate-300',
    });
    if (u.has_errors) {
        items.push({
            label: u.error_row_count ? `Download Errors (${u.error_row_count})` : 'Download Errors',
            icon: <AlertTriangle className="h-4 w-4 shrink-0" />,
            onClick: () => onDownloadErrors(storeId, u.id),
            className: 'text-amber-600 dark:text-amber-400',
        });
    }
    items.push({ divider: true });
    items.push({
        label: 'Delete',
        icon: <Trash2 className="h-4 w-4 shrink-0" />,
        onClick: () => onDelete(u),
        disabled: deletingUploadId === u.id,
        className: 'text-rose-600 dark:text-rose-400',
    });

    const menu = open && createPortal(
        <div
            ref={menuRef}
            role="menu"
            style={{ position: 'fixed', top: menuPos.top, right: menuPos.right, zIndex: 99999 }}
            className="min-w-[14rem] rounded-xl border border-slate-200/90 bg-white py-1.5 shadow-xl shadow-slate-900/10 dark:border-slate-600 dark:bg-slate-900 dark:shadow-black/40 overflow-visible"
        >
            {items.map((item, i) =>
                item.divider ? (
                    <div key={i} className="my-1.5 border-t border-slate-100 dark:border-slate-700/80" role="separator" />
                ) : (
                    <button
                        key={i}
                        type="button"
                        role="menuitem"
                        disabled={item.disabled}
                        onClick={() => { setOpen(false); item.onClick(); }}
                        className={`flex w-full items-center gap-3 whitespace-nowrap px-4 py-2.5 text-left text-sm font-medium transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/90 disabled:cursor-not-allowed disabled:opacity-40 ${item.className}`}
                    >
                        {item.icon}
                        <span>{item.label}</span>
                    </button>
                )
            )}
        </div>,
        document.body,
    );

    return (
        <>
            <button
                ref={triggerRef}
                type="button"
                aria-haspopup="menu"
                aria-expanded={open}
                onClick={() => {
                    setOpen((o) => {
                        if (!o && triggerRef.current) {
                            const r = triggerRef.current.getBoundingClientRect();
                            setMenuPos({ top: r.bottom + 6, right: Math.max(8, window.innerWidth - r.right) });
                        }
                        return !o;
                    });
                }}
                className="inline-flex size-8 items-center justify-center rounded-lg text-slate-500 ring-offset-2 ring-offset-white transition hover:bg-slate-100 hover:text-slate-800 focus:outline-none focus:ring-2 focus:ring-accent-500 dark:text-slate-400 dark:ring-offset-slate-900 dark:hover:bg-slate-800 dark:hover:text-slate-200"
                title="Actions"
            >
                <MoreVertical className="h-4 w-4" />
            </button>
            {menu}
        </>
    );
}

function formatLastSync(lastSync) {
    if (!lastSync) return '—';
    const d = new Date(lastSync);
    const now = new Date();
    const diff = Math.floor((now - d) / 60000);
    if (diff < 1) return 'Just now';
    if (diff < 60) return `${diff}m ago`;
    const h = Math.floor(diff / 60);
    if (h < 24) return `${h}h ago`;
    return `${Math.floor(h / 24)}d ago`;
}

function formatLastStatus(product) {
    const st = product.sync_status || 'pending';
    if (st === 'synced' && product.last_sync_time) {
        return `Sync · ${formatLastSync(product.last_sync_time)}`;
    }
    if (product.last_scrape_time) {
        return `Scrape · ${formatLastSync(product.last_scrape_time)}`;
    }
    return '—';
}

/** Fallback when API does not send Excel-aligned margin_display (legacy). */
function calcMargin(vendorPrice, storePrice) {
    if (!vendorPrice || !storePrice || parseFloat(vendorPrice) === 0) return '—';
    const v = parseFloat(vendorPrice);
    const s = parseFloat(storePrice);
    return `+${((s - v) / v * 100).toFixed(0)}%`;
}

function formatMarginCell(product) {
    const md = product.margin_display ?? product.marginDisplay;
    if (md != null && md !== '') {
        return md;
    }
    return calcMargin(product.vendor_price, product.store_price);
}

function formatPrice(val) {
    if (val == null || val === '') return '—';
    const n = parseFloat(val);
    return isNaN(n) ? '—' : `$${n.toFixed(2)}`;
}

/**
 * Pull a normalized list of per-vendor progress summaries from the server's
 * scrape-progress payload.
 *
 * The backend returns a ``vendors`` dict keyed by code (e.g. ``heb``,
 * ``costco``) with a uniform shape — see ``CatalogScrapeProgressView`` in
 * ``backend/catalog/views.py``. If an older server build is in play we fall
 * back to synthesizing a single HEB entry from the legacy flat ``heb_*``
 * keys so the UI keeps working during rollout.
 */
function getVendorSummaries(progress) {
    if (!progress) return [];
    if (progress.vendors && typeof progress.vendors === 'object') {
        return Object.entries(progress.vendors)
            .filter(([, v]) => v && v.has_products)
            .map(([code, v]) => ({ code, ...v }));
    }
    if (progress.has_heb) {
        return [{
            code: 'heb',
            label: 'HEB',
            has_products: true,
            total: progress.heb_total,
            scraped: progress.heb_scraped,
            pending: progress.heb_pending,
            by_status: progress.heb_by_status,
            pct: progress.heb_pct,
            last_ingest_at: progress.heb_last_ingest_at,
            ingested_last_5m: progress.heb_ingested_last_5m,
            ingested_last_24h: progress.heb_ingested_last_24h,
            job: progress.heb_job,
            queue: progress.heb_queue,
        }];
    }
    return [];
}

/**
 * Pick the vendor that should drive the Scrape button label and tracking
 * state when a store has products from multiple desktop-runner vendors.
 * Prefer one that still has pending work; otherwise fall back to the first.
 */
function getActiveVendor(vendors) {
    if (!Array.isArray(vendors) || vendors.length === 0) return null;
    return vendors.find((v) => (v.pending || 0) > 0) || vendors[0];
}

/**
 * Status strip rendered above the product table for stores with products
 * from a desktop-runner vendor (HEB, Costco, …).
 *
 * Shows, live, whether the desktop runner is uploading prices to this server:
 *   - progress bar with scraped/total
 *   - last ingest upload (relative time)
 *   - 5-min / 24-h ingest counts
 *   - tracking state (so the user knows the Scrape button is intentionally
 *     still working in the background)
 */
function VendorProgressStrip({ vendor, tracking, onStopScrape, stopping }) {
    if (!vendor || !vendor.has_products) return null;
    const label = vendor.label || (vendor.code || 'vendor').toUpperCase();
    const runner = vendor.runner || 'live';
    const runnerLabel = runner === 'desktop'
        ? 'desktop runner'
        : runner === 'server'
            ? 'feed ingest'
            : 'live scrape';
    const pct = Math.max(0, Math.min(100, Number(vendor.pct || 0)));
    const recent5 = vendor.ingested_last_5m || 0;
    const recent24 = vendor.ingested_last_24h || 0;
    const lastAgo = formatLastSync(vendor.last_ingest_at);
    const barColor = pct >= 100
        ? 'bg-emerald-500'
        : recent5 > 0
            ? 'bg-accent-500'
            : 'bg-amber-500';

    const job = vendor.job;
    const queue = vendor.queue;
    const jobStatus = job?.status;
    const isPending = jobStatus === 'pending';
    const isClaimed = jobStatus === 'claimed';
    const isActive = isPending || isClaimed;
    const ahead = queue?.ahead_count || 0;
    const etaLabel = formatEtaShort(queue?.eta_seconds);
    const runningOther = queue?.currently_running && !queue.currently_running.is_this_store
        ? queue.currently_running.store_name
        : null;

    let statusPill = null;
    if (isPending) {
        statusPill = (
            <span className="ml-2 inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700 dark:bg-amber-900/30 dark:text-amber-300">
                queued
            </span>
        );
    } else if (isClaimed) {
        statusPill = (
            <span className="ml-2 inline-flex items-center rounded-full bg-accent-100 px-2 py-0.5 text-xs font-medium text-accent-700 dark:bg-accent-900/30 dark:text-accent-300">
                scraping
            </span>
        );
    } else if (tracking) {
        statusPill = (
            <span className="ml-2 inline-flex items-center rounded-full bg-accent-100 px-2 py-0.5 text-xs font-medium text-accent-700 dark:bg-accent-900/30 dark:text-accent-300">
                tracking live
            </span>
        );
    }

    let queueLine = null;
    if (isPending) {
        const parts = [];
        if (ahead > 0) {
            parts.push(`${ahead} store${ahead === 1 ? '' : 's'} ahead of you`);
        } else {
            parts.push('next in line');
        }
        if (runningOther) parts.push(`scraping "${runningOther}" now`);
        if (etaLabel) {
            parts.push(`starts in ~${etaLabel}`);
        } else if (ahead > 0) {
            parts.push('start time TBD — depends on the runs ahead');
        }
        queueLine = (
            <p className="text-xs text-amber-600 dark:text-amber-300 mt-0.5">
                Pending — {parts.join(' · ')}. Your job will run automatically once the queue clears; no need to retry.
            </p>
        );
    } else if (isClaimed) {
        queueLine = (
            <p className="text-xs text-accent-600 dark:text-accent-300 mt-0.5">
                Running now — prices will populate as the {runnerLabel} uploads them. Safe to close this tab.
            </p>
        );
    }

    return (
        <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-4 mb-4 shadow-sm">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-center gap-3">
                    <span className={`inline-flex h-2.5 w-2.5 rounded-full ${recent5 > 0 ? 'bg-emerald-500 animate-pulse' : 'bg-slate-300 dark:bg-slate-600'}`} />
                    <div>
                        <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                            {label} {runnerLabel}
                            {statusPill}
                        </h3>
                        <p className="text-xs text-slate-500 dark:text-slate-400">
                            {vendor.scraped || 0}/{vendor.total || 0} products populated · last upload {lastAgo} · {recent5} in last 5 min · {recent24} in last 24 h
                        </p>
                        {queueLine}
                    </div>
                </div>
                {isActive && (
                    <button
                        type="button"
                        onClick={onStopScrape}
                        disabled={stopping}
                        className="self-start rounded-md border border-rose-200 px-3 py-1.5 text-xs font-medium text-rose-600 hover:bg-rose-50 disabled:opacity-60 dark:border-rose-800 dark:text-rose-300 dark:hover:bg-rose-900/30"
                    >
                        {stopping ? 'Stopping…' : 'Stop Scraping'}
                    </button>
                )}
            </div>
            <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                <div
                    className={`h-full rounded-full transition-all duration-500 ${barColor}`}
                    style={{ width: `${pct}%` }}
                />
            </div>
        </div>
    );
}

export default function Catalog() {
    const [storeList, setStoreList] = useState([]);
    const [marketplaces, setMarketplaces] = useState([]);
    const [selectedStore, setSelectedStore] = useState('');
    const [selectedMarketplace, setSelectedMarketplace] = useState('');
    const [viewMode, setViewMode] = useState('stores'); // 'stores' | 'history' | 'products' | 'logs'
    const [uploads, setUploads] = useState([]);
    const [products, setProducts] = useState([]);
    const [search, setSearch] = useState('');
    const [statusFilter, setStatusFilter] = useState('');
    const [loading, setLoading] = useState(true);
    const [uploadsLoading, setUploadsLoading] = useState(false);
    const [uploadsError, setUploadsError] = useState('');
    const [uploadsReloadNonce, setUploadsReloadNonce] = useState(0);
    const uploadsFetchGenRef = useRef(0);
    const [productsLoading, setProductsLoading] = useState(false);
    const [uploading, setUploading] = useState(false);
    const [syncing, setSyncing] = useState(false);
    const [flowStatus, setFlowStatus] = useState(''); // file uploaded | ready to sync | syncing | success | failed
    const [message, setMessage] = useState('');
    const [uploadModalOpen, setUploadModalOpen] = useState(false);
    const [deleteStoreConfirm, setDeleteStoreConfirm] = useState(null);
    const [deletingStoreId, setDeletingStoreId] = useState(null);
    const [resettingId, setResettingId] = useState(null);
    const [syncingUploadId, setSyncingUploadId] = useState(null);
    const [scraping, setScraping] = useState(false);
    const [scrapingUploadId, setScrapingUploadId] = useState(null);
    const [deleteUploadConfirm, setDeleteUploadConfirm] = useState(null);
    const [deletingUploadId, setDeletingUploadId] = useState(null);
    const [modalFile, setModalFile] = useState(null);
    const [modalTemplate, setModalTemplate] = useState('standard');
    const [progress, setProgress] = useState(0);
    const progressRef = useRef(null);
    const [currentPage, setCurrentPage] = useState(1);
    const PRODUCTS_PER_PAGE = 10;
    const [exportScope, setExportScope] = useState('all');
    const [exportDownloading, setExportDownloading] = useState(false);
    const [manualPushLoading, setManualPushLoading] = useState(false);
    const [criticalModalOpen, setCriticalModalOpen] = useState(false);
    const [criticalLoading, setCriticalLoading] = useState(false);
    const [activityLogs, setActivityLogs] = useState([]);
    const [logsLoading, setLogsLoading] = useState(false);
    const [liveRefreshUntil, setLiveRefreshUntil] = useState(0);

    // ---- Live scrape progress (used for HEB ingest tracking + status strip) ----
    // scrapeProgress is the latest GET /catalog/scrape/progress/ payload.
    // trackingScrape keeps the Scrape button in a "working" state until every
    // HEB mapping has been populated from the desktop runner's ingest feed, so
    // clicking it doesn't flash "0/879 done" and silently return to idle.
    const [scrapeProgress, setScrapeProgress] = useState(null);
    const [trackingScrape, setTrackingScrape] = useState(false);
    const trackingScrapeRef = useRef(false);
    useEffect(() => { trackingScrapeRef.current = trackingScrape; }, [trackingScrape]);
    const [stoppingScrape, setStoppingScrape] = useState(false);
    // Row IDs whose sync_status just changed — used to flash the row yellow.
    const [flashingRowIds, setFlashingRowIds] = useState(() => new Set());
    const prevStatusRef = useRef(new Map()); // product.id -> last seen sync_status
    const flashTimersRef = useRef(new Map()); // product.id -> timeout id

    const { setSidebarActivity, clearSidebarActivity, clearCatalogActivities } = useSidebarActivity();

    const selectedStoreData = storeList.find((s) => s.id === selectedStore);

    useEffect(
        () => () => {
            clearCatalogActivities();
        },
        [clearCatalogActivities],
    );

    const refreshLiveData = useCallback(() => {
        if (!selectedStore) return;
        getCatalogStores(selectedMarketplace || null).then((r) => setStoreList(Array.isArray(r.data) ? r.data : []));
        getCatalogUploads(selectedStore)
            .then((r) => setUploads(Array.isArray(r.data) ? r.data : []))
            .catch(() => {});
        if (viewMode === 'products') {
            getProducts(selectedStore).then((r) => setProducts(Array.isArray(r.data) ? r.data : []));
        } else if (viewMode === 'logs') {
            getCatalogActivityLogs(selectedStore).then((r) =>
                setActivityLogs(Array.isArray(r.data) ? r.data : []));
        }
    }, [selectedStore, selectedMarketplace, viewMode]);

    const fetchUploadHistory = useCallback((storeId, signal) => {
        const id = ++uploadsFetchGenRef.current;
        setUploadsLoading(true);
        setUploadsError('');
        getCatalogUploads(storeId, { signal })
            .then((res) => {
                if (id !== uploadsFetchGenRef.current) return;
                setUploads(Array.isArray(res.data) ? res.data : []);
            })
            .catch((err) => {
                if (err.code === 'ERR_CANCELED' || err.name === 'CanceledError' || err.name === 'AbortError') return;
                if (id !== uploadsFetchGenRef.current) return;
                setUploads([]);
                setUploadsError(formatCatalogError(err) || 'Could not load upload history.');
            })
            .finally(() => {
                if (id !== uploadsFetchGenRef.current) return;
                setUploadsLoading(false);
            });
    }, []);

    useEffect(() => {
        getMarketplaces()
            .then((res) => setMarketplaces(Array.isArray(res.data?.results || res.data) ? (res.data.results || res.data) : []))
            .catch(() => setMarketplaces([]));
    }, []);

    useEffect(() => {
        setLoading(true);
        getCatalogStores(selectedMarketplace || null)
            .then((res) => setStoreList(Array.isArray(res.data) ? res.data : []))
            .catch(() => setStoreList([]))
            .finally(() => setLoading(false));
    }, [selectedMarketplace]);

    useEffect(() => {
        if (!selectedStore || viewMode !== 'history') return undefined;
        const ac = new AbortController();
        fetchUploadHistory(selectedStore, ac.signal);
        return () => ac.abort();
    }, [selectedStore, viewMode, uploadsReloadNonce, fetchUploadHistory]);

    useEffect(() => {
        if (!selectedStore || viewMode !== 'products') return;
        setProductsLoading(true);
        setMessage('');
        getProducts(selectedStore)
            .then((res) => setProducts(Array.isArray(res.data) ? res.data : []))
            .catch((err) => {
                setProducts([]);
                setMessage(formatCatalogError(err));
            })
            .finally(() => setProductsLoading(false));
    }, [selectedStore, viewMode]);

    useEffect(() => {
        if (!selectedStore || viewMode !== 'logs') return;
        setLogsLoading(true);
        setMessage('');
        getCatalogActivityLogs(selectedStore)
            .then((res) => setActivityLogs(Array.isArray(res.data) ? res.data : []))
            .catch((err) => {
                setActivityLogs([]);
                setMessage(formatCatalogError(err));
            })
            .finally(() => setLogsLoading(false));
    }, [selectedStore, viewMode]);

    useEffect(() => {
        if (loading && !selectedStore) {
            setSidebarActivity('catalog-stores', {
                title: 'Loading stores',
                description: 'Fetching your stores so you can open the catalog.',
            });
        } else {
            clearSidebarActivity('catalog-stores');
        }
    }, [loading, selectedStore, setSidebarActivity, clearSidebarActivity]);

    useEffect(() => {
        if (uploadsLoading && selectedStore && viewMode === 'history') {
            setSidebarActivity('catalog-uploads', {
                title: 'Upload history',
                description: 'Loading past file uploads for this store from the server.',
            });
        } else {
            clearSidebarActivity('catalog-uploads');
        }
    }, [uploadsLoading, selectedStore, viewMode, setSidebarActivity, clearSidebarActivity]);

    useEffect(() => {
        if (productsLoading && selectedStore && viewMode === 'products') {
            setSidebarActivity('catalog-products', {
                title: 'Product listings',
                description:
                    'Downloading all products (paginated). Large stores can take a minute while the API returns each page.',
            });
        } else {
            clearSidebarActivity('catalog-products');
        }
    }, [productsLoading, selectedStore, viewMode, setSidebarActivity, clearSidebarActivity]);

    useEffect(() => {
        if (logsLoading && selectedStore && viewMode === 'logs') {
            setSidebarActivity('catalog-logs', {
                title: 'Activity logs',
                description: 'Loading recent catalog and sync activity for this store.',
            });
        } else {
            clearSidebarActivity('catalog-logs');
        }
    }, [logsLoading, selectedStore, viewMode, setSidebarActivity, clearSidebarActivity]);

    useEffect(() => {
        if (exportDownloading) {
            setSidebarActivity('catalog-export', {
                title: 'Export',
                description: 'Building your CSV file. The download will start when ready.',
            });
        } else {
            clearSidebarActivity('catalog-export');
        }
    }, [exportDownloading, setSidebarActivity, clearSidebarActivity]);

    useEffect(() => {
        if (criticalLoading) {
            setSidebarActivity('catalog-critical', {
                title: 'Critical action',
                description: 'Updating stock and marketplace. Please wait.',
            });
        } else {
            clearSidebarActivity('catalog-critical');
        }
    }, [criticalLoading, setSidebarActivity, clearSidebarActivity]);

    useEffect(() => {
        if (flowStatus === 'syncing' || flowStatus === 'scraping') {
            const title =
                flowStatus === 'scraping' ? 'Scraping vendor data' : 'Sync & marketplace';
            setSidebarActivity('catalog-flow', {
                title,
                description:
                    message ||
                    (flowStatus === 'scraping'
                        ? 'Re-fetching vendor price and stock from your URLs.'
                        : 'Talking to the marketplace and background workers.'),
                progress: progress > 0 ? progress : null,
            });
        } else {
            clearSidebarActivity('catalog-flow');
        }
    }, [flowStatus, message, progress, setSidebarActivity, clearSidebarActivity]);

    useEffect(() => {
        if (flowStatus === 'success') {
            // Keep UI fresh after success: Celery can finish a few seconds after the API returns,
            // and store-wide scrapes may update many rows.
            setLiveRefreshUntil(Date.now() + 120000);
        }
    }, [flowStatus]);

    useEffect(() => {
        if (!selectedStore) return undefined;
        const activeFlow = flowStatus === 'syncing' || flowStatus === 'scraping';
        const inGraceWindow = liveRefreshUntil > Date.now();
        // While we're tracking an HEB scrape run or viewing the products table
        // we also want rows to update live as the desktop runner posts prices.
        const watchingProducts = viewMode === 'products';
        if (!activeFlow && !inGraceWindow && !trackingScrape && !watchingProducts) return undefined;

        refreshLiveData();
        const intervalId = setInterval(refreshLiveData, 5000);
        let timeoutId = null;
        if (!activeFlow && !trackingScrape && !watchingProducts && inGraceWindow) {
            timeoutId = setTimeout(() => clearInterval(intervalId), Math.max(0, liveRefreshUntil - Date.now()));
        }

        return () => {
            clearInterval(intervalId);
            if (timeoutId) clearTimeout(timeoutId);
        };
    }, [selectedStore, flowStatus, liveRefreshUntil, trackingScrape, viewMode, refreshLiveData]);

    // Pull scrape-progress every 5s while the user is looking at products,
    // actively scraping, or we are still "tracking" an HEB scrape run.
    useEffect(() => {
        if (!selectedStore) return undefined;
        const activeFlow = flowStatus === 'syncing' || flowStatus === 'scraping';
        const shouldPoll =
            activeFlow
            || trackingScrape
            || viewMode === 'products'
            || liveRefreshUntil > Date.now();
        if (!shouldPoll) return undefined;

        let cancelled = false;
        const fetchOnce = () => {
            getScrapeProgress(selectedStore)
                .then((res) => {
                    if (cancelled) return;
                    setScrapeProgress(res.data || null);
                })
                .catch(() => { /* transient — just ignore and retry */ });
        };
        fetchOnce();
        const intervalId = setInterval(fetchOnce, 5000);
        return () => {
            cancelled = true;
            clearInterval(intervalId);
        };
    }, [selectedStore, flowStatus, trackingScrape, viewMode, liveRefreshUntil]);

    // Auto-stop tracking when every desktop-runner vendor the store uses has
    // either completed, failed, been cancelled, or has no pending rows left.
    // Works uniformly for HEB, Costco, and any future ingest-only vendor.
    useEffect(() => {
        if (!trackingScrape || !scrapeProgress) return;
        const vendors = getVendorSummaries(scrapeProgress);
        if (vendors.length === 0) {
            setTrackingScrape(false);
            return;
        }
        const active = getActiveVendor(vendors);
        const activeLabel = active?.label || (active?.code || 'vendor').toUpperCase();
        const jobStatus = active?.job?.status;
        if (jobStatus === 'cancelled') {
            setTrackingScrape(false);
            setFlowStatus('');
            setMessage(`${activeLabel} scrape was cancelled.`);
            return;
        }
        if (jobStatus === 'failed') {
            setTrackingScrape(false);
            setFlowStatus('failed');
            setMessage(`${activeLabel} scrape failed on the desktop runner.`);
            return;
        }
        const allDone = vendors.every((v) => (v.pending || 0) === 0 && (v.total || 0) > 0);
        if (allDone) {
            setTrackingScrape(false);
            setFlowStatus('success');
            const parts = vendors.map(
                (v) => `${v.total} ${v.label || v.code.toUpperCase()}`,
            );
            setMessage(
                `All ${parts.join(' + ')} product(s) populated from the desktop runner.`,
            );
        }
    }, [trackingScrape, scrapeProgress]);

    // Whenever the products list updates, flash any row whose sync_status just
    // changed. A brief yellow background tells the user "this just updated".
    useEffect(() => {
        if (!Array.isArray(products) || products.length === 0) return;
        const prev = prevStatusRef.current;
        const changed = [];
        for (const p of products) {
            const id = p.id;
            const nowStatus = p.sync_status || 'pending';
            const before = prev.get(id);
            if (before !== undefined && before !== nowStatus) {
                changed.push(id);
            }
            prev.set(id, nowStatus);
        }
        if (changed.length === 0) return;
        setFlashingRowIds((old) => {
            const next = new Set(old);
            for (const id of changed) next.add(id);
            return next;
        });
        for (const id of changed) {
            const existing = flashTimersRef.current.get(id);
            if (existing) clearTimeout(existing);
            const t = setTimeout(() => {
                setFlashingRowIds((old) => {
                    if (!old.has(id)) return old;
                    const next = new Set(old);
                    next.delete(id);
                    return next;
                });
                flashTimersRef.current.delete(id);
            }, 1600);
            flashTimersRef.current.set(id, t);
        }
    }, [products]);

    // Clean up any in-flight flash timers on unmount.
    useEffect(() => () => {
        for (const t of flashTimersRef.current.values()) clearTimeout(t);
        flashTimersRef.current.clear();
    }, []);

    const handleBackToStores = () => {
        setSelectedStore('');
        setViewMode('stores');
        setFlowStatus('');
        setMessage('');
    };

    const handleBackToHistory = () => {
        setViewMode('history');
        setFlowStatus('');
    };

    const retryUploadHistory = () => setUploadsReloadNonce((n) => n + 1);

    const handleViewProducts = () => {
        setViewMode('products');
    };

    const handleDeleteStore = (store) => {
        if (!store) return;
        setDeletingStoreId(store.id);
        deleteStore(store.id)
            .then(() => {
                setDeleteStoreConfirm(null);
                if (selectedStore === store.id) {
                    setSelectedStore('');
                    setViewMode('stores');
                }
                setMessage(`Store "${store.name}" deleted.`);
                getCatalogStores(selectedMarketplace || null).then((r) => setStoreList(Array.isArray(r.data) ? r.data : []));
            })
            .catch((err) => setMessage(formatCatalogError(err) || 'Failed to delete store'))
            .finally(() => setDeletingStoreId(null));
    };

    const handleResetSyncStatus = (product) => {
        if (!selectedStore) return;
        setResettingId(product.id);
        resetProductSyncStatus(selectedStore, product.id)
            .then(() => {
                setMessage(`Reset ${product.sku}. Ready to retry sync.`);
                getProducts(selectedStore).then((r) => setProducts(Array.isArray(r.data) ? r.data : []));
            })
            .catch((err) => setMessage(formatCatalogError(err) || 'Reset failed'))
            .finally(() => setResettingId(null));
    };

    const startProgress = useCallback(() => {
        setProgress(0);
        if (progressRef.current) clearInterval(progressRef.current);
        let p = 0;
        let tick = 0;
        progressRef.current = setInterval(() => {
            tick++;
            const speed = tick < 5 ? 0.02 : tick < 20 ? 0.008 : tick < 60 ? 0.003 : 0.001;
            p += (95 - p) * speed;
            setProgress(Math.min(Math.round(p), 95));
        }, 500);
    }, []);

    const finishProgress = useCallback((success = true) => {
        if (progressRef.current) clearInterval(progressRef.current);
        progressRef.current = null;
        setProgress(success ? 100 : 0);
        if (success) setTimeout(() => setProgress(0), 1500);
    }, []);

    const handleUpload = (file) => {
        if (!selectedStore) {
            setMessage('Please select a store first.');
            return;
        }
        setUploading(true);
        setFlowStatus('syncing');
        setMessage('Uploading file and syncing products…');
        startProgress();
        uploadCatalog(file, selectedStore)
            .then((res) => {
                setUploadModalOpen(false);
                setModalFile(null);
                setModalTemplate('standard');
                getCatalogUploads(selectedStore).then((r) => setUploads(Array.isArray(r.data) ? r.data : []));
                const latestUploadId = res.data?.upload_id || null;
                return triggerCatalogSync(selectedStore, false, latestUploadId, { autoScrape: true });
            })
            .then((syncRes) => {
                const added = syncRes?.data?.added ?? 0;
                const scrape = syncRes?.data?.scrape;
                let scrapeMsg = '';
                if (scrape && !scrape.error && !scrape.skipped) {
                    const ok = scrape.rows_succeeded ?? 0;
                    const proc = scrape.rows_processed ?? 0;
                    scrapeMsg = ` Vendor prices scraped: ${ok}/${proc} row(s).`;
                } else if (scrape?.skipped) {
                    scrapeMsg = ' (Scrape skipped.)';
                } else if (scrape?.error) {
                    scrapeMsg = ` Scrape warning: ${scrape.error}`;
                }
                finishProgress(true);
                setFlowStatus('success');
                setMessage(
                    (added > 0 ? `Upload & sync complete. ${added} product(s) created.` : 'Upload & sync complete.') + scrapeMsg,
                );
                getCatalogUploads(selectedStore).then((r) => setUploads(Array.isArray(r.data) ? r.data : []));
                getCatalogStores(selectedMarketplace || null).then((r) => setStoreList(Array.isArray(r.data) ? r.data : []));
            })
            .catch((err) => {
                finishProgress(false);
                setFlowStatus('failed');
                setMessage(formatCatalogError(err) || 'Upload or sync failed');
            })
            .finally(() => setUploading(false));
    };

    const handleSync = (uploadId = null) => {
        if (!selectedStore) return;
        setSyncing(true);
        setFlowStatus('syncing');
        setSyncingUploadId(uploadId);
        setMessage('Syncing with marketplace and fetching vendor prices…');
        startProgress();

        const MAX_RETRIES = 3;
        let attempt = 0;

        const runSync = () => {
            attempt++;
            return triggerCatalogSync(selectedStore, false, uploadId, { autoScrape: true })
                .then((res) => {
                const added = res?.data?.added ?? 0;
                const scrape = res?.data?.scrape;
                let scrapeMsg = '';
                if (scrape && !scrape.error && !scrape.skipped) {
                    const ok = scrape.rows_succeeded ?? 0;
                    const proc = scrape.rows_processed ?? 0;
                    scrapeMsg = ` Vendor prices scraped: ${ok}/${proc} row(s).`;
                } else if (scrape?.skipped) {
                    scrapeMsg = ' (Scrape skipped — run “Start Scraping” after a successful sync.)';
                } else if (scrape?.error) {
                    scrapeMsg = ` Scrape warning: ${scrape.error}`;
                }
                finishProgress(true);
                setFlowStatus('success');
                setMessage(
                    (added > 0 ? `Sync complete. ${added} product(s) created.` : 'Sync complete.') + scrapeMsg,
                );
                getCatalogUploads(selectedStore).then((r) => setUploads(Array.isArray(r.data) ? r.data : []));
                getCatalogStores(selectedMarketplace || null).then((r) => setStoreList(Array.isArray(r.data) ? r.data : []));
                })
                .catch((err) => {
                    const isNetErr = !err.response || err.code === 'ERR_NETWORK' || err.message === 'Network Error';
                    if (isNetErr && attempt < MAX_RETRIES) {
                        setMessage(`Network hiccup detected, retrying automatically in a few seconds (attempt ${attempt + 1}/${MAX_RETRIES})… Your sync job is still running in the background.`);
                        return new Promise((r) => setTimeout(r, 5000)).then(runSync);
                    }
                    finishProgress(false);
                    if (isNetErr) {
                        setFlowStatus('syncing');
                        setMessage(
                            'The sync job was queued but the browser lost contact with the server. It will keep running in the background — refresh the page in a minute to see the results.',
                        );
                    } else {
                        setFlowStatus('failed');
                        setMessage(formatCatalogError(err));
                    }
                });
        };

        runSync().finally(() => {
            setSyncing(false);
            setSyncingUploadId(null);
        });
    };

    const handleScrape = (uploadId = null) => {
        if (!selectedStore) return;
        setScraping(true);
        setScrapingUploadId(uploadId);
        setFlowStatus('scraping');
        setMessage('Scraping vendor URLs for price and stock…');
        startProgress();

        const MAX_RETRIES = 3;
        let attempt = 0;

        const runScrape = () => {
            attempt++;
            return triggerCatalogScrape(selectedStore, false, uploadId)
                .then((res) => {
                    const ok = res?.data?.rows_succeeded ?? 0;
                    const proc = res?.data?.rows_processed ?? 0;
                    finishProgress(true);

                    // Desktop-runner vendors (HEB, Costco, …) are ingest-only
                    // on the server: the scrape task returns instantly after
                    // promoting whatever the desktop runner has posted so
                    // far. We shift into "tracking" mode and keep the button
                    // busy until every ingest-only mapping is populated. For
                    // stores with no ingest-only vendors, behave as before.
                    return getScrapeProgress(selectedStore)
                        .then((p) => {
                            const progress = p?.data || null;
                            setScrapeProgress(progress);
                            const vendors = getVendorSummaries(progress);
                            const pendingVendors = vendors.filter(
                                (v) => (v.pending || 0) > 0,
                            );
                            const shouldTrack = !uploadId && pendingVendors.length > 0;
                            if (shouldTrack) {
                                setTrackingScrape(true);
                                setFlowStatus('scraping');
                                const parts = pendingVendors.map((v) => {
                                    const lbl = v.label || (v.code || '').toUpperCase();
                                    return `${lbl}: ${v.scraped || 0}/${v.total || 0} populated (${v.pending} remaining)`;
                                });
                                setMessage(
                                    `${parts.join(' · ')} — waiting for the desktop runner to upload the remaining rows. This updates live.`,
                                );
                            } else {
                                setFlowStatus('success');
                                setMessage(
                                    `Scrape complete: ${ok}/${proc} product(s) updated with vendor price/stock. `
                                    + 'Marketplace push runs next in the background when the worker picks it up.',
                                );
                            }
                        })
                        .catch(() => {
                            // Progress endpoint failed — fall back to old behavior.
                            setFlowStatus('success');
                            setMessage(
                                `Scrape complete: ${ok}/${proc} product(s) updated with vendor price/stock. `
                                + 'Marketplace push runs next in the background when the worker picks it up.',
                            );
                        })
                        .finally(() => {
                            getCatalogUploads(selectedStore).then((r) => setUploads(Array.isArray(r.data) ? r.data : []));
                            getCatalogStores(selectedMarketplace || null).then((r) => setStoreList(Array.isArray(r.data) ? r.data : []));
                            if (viewMode === 'products') {
                                getProducts(selectedStore).then((r) => setProducts(Array.isArray(r.data) ? r.data : []));
                            }
                            if (viewMode === 'logs') {
                                getCatalogActivityLogs(selectedStore).then((r) =>
                                    setActivityLogs(Array.isArray(r.data) ? r.data : []));
                            }
                        });
                })
                .catch((err) => {
                    const isNetworkError = !err.response || err.code === 'ERR_NETWORK' || err.message === 'Network Error';
                    if (isNetworkError && attempt < MAX_RETRIES) {
                        setMessage(`Network hiccup detected, retrying automatically in a few seconds (attempt ${attempt + 1}/${MAX_RETRIES})… Your scrape job is still running in the background.`);
                        return new Promise((resolve) => setTimeout(resolve, 5000)).then(runScrape);
                    }
                    finishProgress(false);
                    if (isNetworkError) {
                        setFlowStatus('scraping');
                        setMessage(
                            'The scrape job was queued but the browser lost contact with the server. It will keep running in the background — refresh the page in a minute to see updated prices.',
                        );
                    } else {
                        setFlowStatus('failed');
                        setMessage(formatCatalogError(err));
                    }
                });
        };

        runScrape().finally(() => {
            // Keep `scraping` true while we're tracking desktop-runner
            // progress so the button and spinner stay visible; the
            // trackingScrape effect clears it once every tracked vendor has
            // pending=0.
            if (!trackingScrapeRef.current) {
                setScraping(false);
                setScrapingUploadId(null);
            }
        });
    };

    // When tracking ends (either pending hit 0, or user cancelled), release
    // the Scrape button.
    useEffect(() => {
        if (!trackingScrape) {
            setScraping(false);
            setScrapingUploadId(null);
        }
    }, [trackingScrape]);

    const handleStopScrape = () => {
        if (!selectedStore || stoppingScrape) return;
        setStoppingScrape(true);
        const activeVendor = getActiveVendor(getVendorSummaries(scrapeProgress));
        const vendorLabel = activeVendor?.label
            || (activeVendor?.code || 'desktop').toUpperCase();
        setMessage(`Stopping the ${vendorLabel} scrape…`);
        cancelCatalogScrape(selectedStore)
            .then((res) => {
                const cancelledId = res?.data?.cancelled;
                setTrackingScrape(false);
                setFlowStatus(cancelledId ? 'success' : '');
                setMessage(
                    cancelledId
                        ? `${vendorLabel} scrape cancelled. The desktop runner will stop on its next check.`
                        : `No active ${vendorLabel} scrape to stop.`,
                );
                getScrapeProgress(selectedStore)
                    .then((p) => setScrapeProgress(p.data || null))
                    .catch(() => {});
            })
            .catch((err) => {
                setMessage(formatCatalogError(err) || 'Failed to stop the scrape.');
            })
            .finally(() => {
                setStoppingScrape(false);
            });
    };

    const handleExportProducts = () => {
        if (!selectedStore) return;
        let syncStatus;
        if (exportScope === 'failed') syncStatus = 'failed';
        else if (exportScope === 'filter') {
            if (!statusFilter) {
                setMessage('Choose a status in the filter, or switch export scope to “All products”.');
                return;
            }
            syncStatus = statusFilter;
        }
        setExportDownloading(true);
        exportCatalogProducts(selectedStore, { syncStatus })
            .then(() => setMessage('Catalog export downloaded.'))
            .catch(() => setMessage('Export failed. Try again.'))
            .finally(() => setExportDownloading(false));
    };

    const handleManualPushListings = () => {
        if (!selectedStore) return;
        setManualPushLoading(true);
        setFlowStatus('syncing');
        setMessage('Pushing scraped/synced listings to marketplace (no vendor scrape)…');
        startProgress();
        triggerCatalogPushListings(selectedStore, false)
            .then((res) => {
                const d = res.data || {};
                finishProgress(true);
                setFlowStatus('success');
                const pushed = d.pushed ?? 0;
                const failed = d.failed ?? 0;
                const skipped = d.skipped_no_listing ?? 0;
                setMessage(
                    `Manual sync complete: ${pushed} pushed, ${failed} failed, ${skipped} skipped (no listing ID). `
                    + 'Scheduled automatic updates for this store are turned off until you enable them again in store settings.',
                );
                if (viewMode === 'products') {
                    getProducts(selectedStore).then((r) => setProducts(Array.isArray(r.data) ? r.data : []));
                }
                getCatalogStores(selectedMarketplace || null).then((r) => setStoreList(Array.isArray(r.data) ? r.data : []));
                if (viewMode === 'logs') {
                    getCatalogActivityLogs(selectedStore).then((r) =>
                        setActivityLogs(Array.isArray(r.data) ? r.data : []));
                }
            })
            .catch((err) => {
                finishProgress(false);
                setFlowStatus('failed');
                setMessage(formatCatalogError(err));
            })
            .finally(() => setManualPushLoading(false));
    };

    const handleCriticalZeroConfirm = () => {
        if (!selectedStore) return;
        setCriticalLoading(true);
        triggerCatalogCriticalZero(selectedStore, false)
            .then((res) => {
                const d = res.data || {};
                setCriticalModalOpen(false);
                setFlowStatus('success');
                setMessage(
                    `Critical action finished: local stock set to 0; ${d.marketplace_push_ok ?? 0} marketplace update(s) ok. Store and schedule are deactivated.`,
                );
                getCatalogStores(selectedMarketplace || null).then((r) => setStoreList(Array.isArray(r.data) ? r.data : []));
                if (viewMode === 'products') {
                    getProducts(selectedStore).then((r) => setProducts(Array.isArray(r.data) ? r.data : []));
                }
            })
            .catch((err) => {
                setFlowStatus('failed');
                setMessage(formatCatalogError(err) || 'Critical action failed.');
            })
            .finally(() => setCriticalLoading(false));
    };

    const handleSyncFromModal = () => {
        const latest = uploads.find((u) => ['pending', 'validated'].includes(u.status));
        if (latest) handleSync(latest.id);
        else handleSync();
    };

    const handleDeleteUpload = (upload) => {
        if (!selectedStore || !upload) return;
        setDeletingUploadId(upload.id);
        deleteCatalogUpload(selectedStore, upload.id)
            .then(() => {
                setDeleteUploadConfirm(null);
                setMessage('Upload and linked products deleted.');
                getCatalogUploads(selectedStore).then((r) => setUploads(Array.isArray(r.data) ? r.data : []));
                getCatalogStores(selectedMarketplace || null).then((r) => setStoreList(Array.isArray(r.data) ? r.data : []));
            })
            .catch((err) => setMessage(formatCatalogError(err) || 'Failed to delete upload'))
            .finally(() => setDeletingUploadId(null));
    };

    const filteredProducts = products.filter((p) => {
        const matchSearch = !search ||
            (p.sku?.toLowerCase().includes(search.toLowerCase())) ||
            (p.title?.toLowerCase().includes(search.toLowerCase())) ||
            (p.vendor_name?.toLowerCase().includes(search.toLowerCase()));
        const matchStatus = !statusFilter || (p.sync_status || 'pending') === statusFilter;
        return matchSearch && matchStatus;
    });

    const totalPages = Math.max(1, Math.ceil(filteredProducts.length / PRODUCTS_PER_PAGE));
    const safePage = Math.min(currentPage, totalPages);
    const paginatedProducts = filteredProducts.slice(
        (safePage - 1) * PRODUCTS_PER_PAGE,
        safePage * PRODUCTS_PER_PAGE,
    );

    useEffect(() => { setCurrentPage(1); }, [search, statusFilter, selectedStore]);

    const hasPendingUpload = uploads.some((u) => ['pending', 'validated'].includes(u.status));

    return (
        <div className="space-y-6">
            <PageHeader
                title="Catalog"
                description="Pick a store, upload a file, and sync listings to your marketplace. Use All stores to choose a different store."
            />

            {(flowStatus || message) && flowStatus !== 'syncing' && flowStatus !== 'scraping' && (
                <div
                    className={`rounded-lg overflow-hidden text-sm ${
                        flowStatus === 'failed'
                            ? 'bg-rose-50 dark:bg-rose-900/20 text-rose-700 dark:text-rose-300'
                            : flowStatus === 'success'
                                ? 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-300'
                                : 'bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300'
                    }`}
                >
                    <div className="flex items-center gap-3 px-4 py-3">
                        <span className="flex-1 min-w-0">{message}</span>
                    </div>
                </div>
            )}

            {/* When a store is selected: toolbar first, then uploads or products */}
            {selectedStore && selectedStoreData && (
                <div className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50/90 dark:bg-slate-800/60 px-4 py-3 shadow-sm">
                    <div className="flex w-full flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                        <div className="flex flex-wrap items-center gap-2 sm:gap-3">
                            <button
                                type="button"
                                onClick={handleBackToStores}
                                className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 px-3 py-1.5 text-sm font-medium text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 transition"
                            >
                                <ChevronLeft className="h-4 w-4" />
                                All stores
                            </button>
                            <span className="hidden sm:inline text-slate-300 dark:text-slate-600 select-none">|</span>
                            <div className="flex min-w-0 items-center gap-2">
                                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-slate-200 dark:bg-slate-700">
                                    <Store className="h-4 w-4 text-slate-600 dark:text-slate-400" />
                                </div>
                                <div className="min-w-0">
                                    <p className="truncate font-semibold text-slate-900 dark:text-slate-100">{selectedStoreData.name}</p>
                                    <p className="truncate text-xs text-slate-500 dark:text-slate-400">
                                        {selectedStoreData.marketplace_name || '—'}
                                        {selectedStoreData.product_count != null &&
                                            ` · ${selectedStoreData.product_count} product${selectedStoreData.product_count !== 1 ? 's' : ''}`}
                                        {selectedStoreData.schedule_active === false && (
                                            <span className="text-amber-600 dark:text-amber-400"> · Schedule off</span>
                                        )}
                                    </p>
                                </div>
                            </div>
                        </div>
                        <div className="flex flex-wrap items-center gap-2 lg:justify-end">
                            <div className="inline-flex rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 p-0.5">
                                <button
                                    type="button"
                                    onClick={() => setViewMode('history')}
                                    className={`rounded-md px-3 py-1.5 text-xs font-semibold transition ${
                                        viewMode === 'history'
                                            ? 'bg-accent-500 text-white shadow-sm'
                                            : 'text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800'
                                    }`}
                                >
                                    Upload history
                                </button>
                                <button
                                    type="button"
                                    onClick={handleViewProducts}
                                    className={`rounded-md px-3 py-1.5 text-xs font-semibold transition ${
                                        viewMode === 'products'
                                            ? 'bg-accent-500 text-white shadow-sm'
                                            : 'text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800'
                                    }`}
                                >
                                    Products
                                </button>
                                <button
                                    type="button"
                                    onClick={() => setViewMode('logs')}
                                    className={`rounded-md px-3 py-1.5 text-xs font-semibold transition ${
                                        viewMode === 'logs'
                                            ? 'bg-accent-500 text-white shadow-sm'
                                            : 'text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800'
                                    }`}
                                >
                                    Logs
                                </button>
                            </div>
                            <Button
                                variant="primary"
                                size="sm"
                                onClick={() => setUploadModalOpen(true)}
                                disabled={uploading}
                            >
                                <UploadCloud className="h-4 w-4 mr-1.5" />
                                Upload &amp; Sync
                            </Button>
                        </div>
                    </div>
                </div>
            )}

            {/* Upload history view (when store selected) */}
            {selectedStore && viewMode === 'history' && (
                <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 overflow-hidden">
                    <div className="border-b border-slate-200 dark:border-slate-700 px-4 py-3">
                        <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Upload history</h2>
                        <p className="text-xs text-slate-500 dark:text-slate-400">Files uploaded for this store; sync or scrape per row.</p>
                    </div>

                    <div className="overflow-x-auto">
                        {uploadsLoading ? (
                            <div className="px-4 py-6">
                                <div className="mb-4 flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                                    <div>
                                        <p className="text-sm font-medium text-slate-800 dark:text-slate-200">
                                            Loading upload history
                                        </p>
                                        <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                                            Requesting the list from the server (up to 90s for slow connections).
                                        </p>
                                    </div>
                                    <div className="flex items-center gap-1.5" aria-hidden>
                                        <span className="inline-block h-1.5 w-1.5 rounded-full bg-accent-500/80 animate-pulse" />
                                        <span
                                            className="inline-block h-1.5 w-1.5 rounded-full bg-accent-500/60 animate-pulse"
                                            style={{ animationDelay: '0.2s' }}
                                        />
                                        <span
                                            className="inline-block h-1.5 w-1.5 rounded-full bg-accent-500/40 animate-pulse"
                                            style={{ animationDelay: '0.4s' }}
                                        />
                                    </div>
                                </div>
                                <table className="table-base w-full">
                                    <thead>
                                        <tr>
                                            <th className="whitespace-nowrap">Date</th>
                                            <th className="whitespace-nowrap">User</th>
                                            <th className="w-[100px] whitespace-nowrap">Vendor</th>
                                            <th className="w-[120px] whitespace-nowrap">Marketplace</th>
                                            <th className="w-[60px] text-right whitespace-nowrap">Items</th>
                                            <th className="min-w-[100px] whitespace-nowrap">Reason</th>
                                            <th className="w-[90px] whitespace-nowrap">Status</th>
                                            <th className="w-[80px] text-right whitespace-nowrap">Actions</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {[0, 1, 2, 3, 4].map((i) => (
                                            <tr key={i}>
                                                <td colSpan={8} className="py-2.5">
                                                    <div className="h-3.5 w-full max-w-full rounded bg-slate-200/80 dark:bg-slate-700/80 animate-pulse" />
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        ) : uploadsError ? (
                            <div className="flex flex-col items-center justify-center gap-4 px-4 py-14 text-center">
                                <p className="text-sm text-rose-600 dark:text-rose-400 max-w-md">{uploadsError}</p>
                                <Button variant="secondary" size="sm" type="button" onClick={retryUploadHistory}>
                                    <RefreshCw className="h-4 w-4 mr-1.5" />
                                    Try again
                                </Button>
                            </div>
                        ) : uploads.length === 0 ? (
                            <EmptyState
                                icon={FileText}
                                title="No uploads yet"
                                description="Upload a catalog file with Upload & Sync in the bar above."
                                action={
                                    <Button
                                        variant="primary"
                                        size="sm"
                                        onClick={() => selectedStore && setUploadModalOpen(true)}
                                        disabled={!selectedStore || uploading}
                                    >
                                        <UploadCloud className="h-4 w-4 mr-1.5" />
                                        Upload &amp; Sync
                                    </Button>
                                }
                            />
                        ) : (
                            <table className="table-base">
                                <thead>
                                    <tr>
                                        <th className="whitespace-nowrap">Date</th>
                                        <th className="whitespace-nowrap">User</th>
                                        <th className="w-[100px] whitespace-nowrap">Vendor</th>
                                        <th className="w-[120px] whitespace-nowrap">Marketplace</th>
                                        <th className="w-[60px] text-right whitespace-nowrap">Items</th>
                                        <th className="min-w-[100px] whitespace-nowrap">Reason</th>
                                        <th className="w-[90px] whitespace-nowrap">Status</th>
                                        <th className="w-[80px] text-right whitespace-nowrap">Actions</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {uploads.map((u) => (
                                        <tr key={u.id}>
                                            <td className="text-sm text-slate-600 dark:text-slate-400 whitespace-nowrap align-middle">
                                                {formatDate(u.created_at)}
                                            </td>
                                            <td className="text-sm align-middle">
                                                <span className="block truncate" title={u.user_name || undefined}>{u.user_name || '—'}</span>
                                            </td>
                                            <td className="text-sm text-slate-600 dark:text-slate-400 align-middle">
                                                {u.vendor_source || (u.vendor_names && [...new Set(u.vendor_names)].filter(Boolean)[0]) || '—'}
                                            </td>
                                            <td className="text-sm align-middle">
                                                {u.marketplace || '—'}
                                            </td>
                                            <td className="text-right text-sm tabular-nums align-middle whitespace-nowrap">{u.processed_rows ?? u.total_rows ?? '—'}</td>
                                            <td className="text-sm text-slate-600 dark:text-slate-400 align-middle whitespace-nowrap" title={u.reason || undefined}>
                                                {u.reason || '—'}
                                            </td>
                                            <td className="align-middle whitespace-nowrap">
                                                <Badge variant={uploadStatusVariant[u.status] || 'warning'}>
                                                    {uploadStatusLabel[u.status] || u.status}
                                                </Badge>
                                            </td>
                                            <td className="text-right align-middle">
                                                <UploadActionsDropdown
                                                    upload={u}
                                                    storeId={selectedStore}
                                                    syncing={syncing}
                                                    scraping={scraping}
                                                    syncingUploadId={syncingUploadId}
                                                    scrapingUploadId={scrapingUploadId}
                                                    deletingUploadId={deletingUploadId}
                                                    onSync={handleSync}
                                                    onScrape={handleScrape}
                                                    onDelete={setDeleteUploadConfirm}
                                                    onDownload={(sid, uid, fname) => downloadCatalogUploadFile(sid, uid, fname).catch(() => setMessage('Failed to download catalog file'))}
                                                    onDownloadErrors={(sid, uid) => downloadCatalogUploadErrors(sid, uid).catch(() => setMessage('Failed to download error file'))}
                                                />
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}
                    </div>

                    {hasPendingUpload && flowStatus === 'ready to sync' && (
                        <div className="p-4 border-t border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/50 space-y-2">
                            <p className="text-xs text-slate-500 dark:text-slate-400">
                                Next step creates products from your file, then automatically scrapes vendor URLs for price and stock (may take a minute for Amazon).
                            </p>
                            <Button variant="primary" onClick={handleSyncFromModal} disabled={syncing}>
                                <RefreshCw className={`h-4 w-4 mr-2 ${syncing ? 'animate-spin' : ''}`} />
                                Sync &amp; Start Scraping
                            </Button>
                        </div>
                    )}
                </div>
            )}

            {/* Desktop-runner ingest status strips — one per vendor the store
                uses (HEB, Costco, …). Backend exposes them uniformly under
                ``scrapeProgress.vendors``. */}
            {selectedStore && viewMode === 'products'
                && getVendorSummaries(scrapeProgress).map((vendor) => (
                    <VendorProgressStrip
                        key={vendor.code}
                        vendor={vendor}
                        tracking={trackingScrape}
                        onStopScrape={handleStopScrape}
                        stopping={stoppingScrape}
                    />
                ))}

            {/* Product table (when View Products clicked) */}
            {selectedStore && viewMode === 'products' && (
                <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 overflow-hidden">
                    <div className="flex flex-col gap-4 p-4 sm:flex-row sm:items-center sm:justify-between border-b border-slate-200 dark:border-slate-700">
                        <div>
                            <h2 className="text-section font-medium text-slate-900 dark:text-slate-100">Product listings</h2>
                            <p className="text-xs text-slate-500 dark:text-slate-400">
                                {filteredProducts.length === products.length
                                    ? `${products.length} products`
                                    : `${filteredProducts.length} of ${products.length} products`}
                            </p>
                        </div>
                        <div className="flex w-full flex-1 flex-col gap-2 lg:flex-row lg:flex-wrap lg:justify-end lg:items-center lg:max-w-none">
                            <div className="flex flex-wrap items-center gap-2">
                                <Button
                                    variant="secondary"
                                    size="sm"
                                    onClick={handleManualPushListings}
                                    disabled={manualPushLoading || scraping || !selectedStore}
                                    title="Push current price/stock to marketplace for Synced / Scrape rows only (no new vendor fetch)"
                                >
                                    <RefreshCw className={`h-4 w-4 mr-1.5 ${manualPushLoading ? 'animate-spin' : ''}`} />
                                    Manual sync
                                </Button>
                                <Button
                                    variant="secondary"
                                    size="sm"
                                    onClick={() => setCriticalModalOpen(true)}
                                    disabled={criticalLoading || !selectedStore}
                                    className="border-rose-200 text-rose-700 hover:bg-rose-50 dark:border-rose-900 dark:text-rose-300 dark:hover:bg-rose-950/40"
                                    title="Emergency: set all listing stock to 0 and turn off store automation"
                                >
                                    <AlertTriangle className="h-4 w-4 mr-1.5" />
                                    Critical action
                                </Button>
                                <div className="flex items-center gap-1.5">
                                    <select
                                        className="rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-2 text-xs text-slate-900 dark:text-slate-100 max-w-[10rem]"
                                        value={exportScope}
                                        onChange={(e) => setExportScope(e.target.value)}
                                        title="What to include in the CSV export"
                                    >
                                        <option value="all">Export: all products</option>
                                        <option value="filter">Export: current filter</option>
                                        <option value="failed">Export: failed only</option>
                                    </select>
                                    <Button
                                        variant="secondary"
                                        size="sm"
                                        onClick={handleExportProducts}
                                        disabled={exportDownloading || !selectedStore}
                                    >
                                        <FileDown className={`h-4 w-4 mr-1.5 ${exportDownloading ? 'opacity-50' : ''}`} />
                                        Export
                                    </Button>
                                </div>
                            </div>
                            {(() => {
                                const vendorList = getVendorSummaries(scrapeProgress);
                                const activeVendor = getActiveVendor(vendorList);
                                const activeLabel = activeVendor?.label
                                    || (activeVendor?.code || '').toUpperCase();
                                const activeJobStatus = activeVendor?.job?.status;
                                const isPending = activeJobStatus === 'pending';
                                const isClaimed = activeJobStatus === 'claimed';
                                const isActive = isPending || isClaimed || trackingScrape;
                                const aheadCount = activeVendor?.queue?.ahead_count || 0;
                                const etaLabel = formatEtaShort(activeVendor?.queue?.eta_seconds);

                                let label = 'Start Scraping';
                                if (isPending) {
                                    label = aheadCount > 0
                                        ? `Pending — ${aheadCount} ahead${etaLabel ? ` (${etaLabel})` : ''}`
                                        : `Pending — next in line${etaLabel ? ` (${etaLabel})` : ''}`;
                                } else if (trackingScrape && activeVendor) {
                                    label = `Scraping ${activeLabel}… ${activeVendor.scraped || 0}/${activeVendor.total || 0} (${activeVendor.pct || 0}%)`;
                                } else if (isClaimed) {
                                    label = 'Scraping…';
                                }

                                let titleText;
                                if (isPending) {
                                    titleText = 'Another store is currently scraping. This store will start automatically when the runner is free.';
                                } else if (isActive) {
                                    titleText = 'Scrape running — use "Stop Scraping" to cancel.';
                                } else if (vendorList.length > 0) {
                                    const labels = vendorList
                                        .map((v) => v.label || (v.code || '').toUpperCase())
                                        .join(' / ');
                                    titleText = `Re-fetch vendor price/stock for all active listings (${labels} use the desktop runner ingest feed).`;
                                } else {
                                    titleText = 'Re-fetch vendor price/stock for all active listings.';
                                }

                                return (
                                    <>
                                        <Button
                                            variant="secondary"
                                            size="sm"
                                            onClick={() => handleScrape(null)}
                                            disabled={scraping || isActive || !selectedStore}
                                            title={titleText}
                                        >
                                            <RefreshCw className={`h-4 w-4 mr-1.5 ${scraping || isActive ? 'animate-spin' : ''}`} />
                                            {label}
                                        </Button>
                                        {isActive && (
                                            <Button
                                                variant="secondary"
                                                size="sm"
                                                onClick={handleStopScrape}
                                                disabled={stoppingScrape}
                                                className="border-rose-200 text-rose-700 hover:bg-rose-50 dark:border-rose-900 dark:text-rose-300 dark:hover:bg-rose-950/40"
                                                title="Cancel the scrape. The desktop runner stops on its next check."
                                            >
                                                {stoppingScrape ? 'Stopping…' : 'Stop Scraping'}
                                            </Button>
                                        )}
                                    </>
                                );
                            })()}
                            <div className="relative flex-1 min-w-[12rem] lg:max-w-xs">
                                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                                <input
                                    className="w-full rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 py-2 pl-9 pr-3 text-sm text-slate-900 dark:text-slate-100 placeholder:text-slate-400 focus:border-accent-500 focus:ring-1 focus:ring-accent-500 outline-none"
                                    placeholder="Search SKU, title, vendor…"
                                    value={search}
                                    onChange={(e) => setSearch(e.target.value)}
                                />
                            </div>
                            <select
                                className="rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 focus:border-accent-500 focus:ring-1 focus:ring-accent-500 outline-none"
                                value={statusFilter}
                                onChange={(e) => setStatusFilter(e.target.value)}
                            >
                                <option value="">All statuses</option>
                                <option value="synced">Synced</option>
                                <option value="scraped">Scrape</option>
                                <option value="needs_attention">Needs attention</option>
                                <option value="pending">Pending</option>
                                <option value="failed">Failed</option>
                            </select>
                        </div>
                    </div>

                    <div className="overflow-x-auto">
                        {productsLoading ? (
                            <div className="flex justify-center py-16">
                                <div className="flex flex-col items-center gap-3">
                                    <div className="h-8 w-8 animate-spin rounded-full border-2 border-slate-200 dark:border-slate-700 border-t-accent-500" />
                                    <p className="text-sm text-slate-500 dark:text-slate-400">Loading products…</p>
                                </div>
                            </div>
                        ) : filteredProducts.length === 0 ? (
                            <EmptyState
                                icon={Package}
                                title="No products"
                                description="Upload a catalog file and run Sync to create products."
                                action={
                                    <Button variant="secondary" size="sm" onClick={handleBackToHistory}>
                                        View upload history
                                    </Button>
                                }
                            />
                        ) : (
                            <table className="table-base">
                                <thead>
                                    <tr>
                                        <th className="whitespace-nowrap">SKU</th>
                                        <th className="whitespace-nowrap">Title</th>
                                        <th className="w-[90px] whitespace-nowrap">Vendor</th>
                                        <th className="w-[80px] whitespace-nowrap text-center">Vendor URL</th>
                                        <th className="w-[90px] whitespace-nowrap text-right">Vendor price</th>
                                        <th className="w-[80px] whitespace-nowrap text-right">Price</th>
                                        <th className="w-[60px] whitespace-nowrap text-right">Stock</th>
                                        <th className="w-[90px] whitespace-nowrap text-center">Status</th>
                                        <th className="w-[70px] whitespace-nowrap text-right">Margin</th>
                                        <th className="w-[100px] whitespace-nowrap">Last Status</th>
                                        <th className="w-[80px] whitespace-nowrap text-right">Actions</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {paginatedProducts.map((product) => {
                                        const status = product.sync_status || 'pending';
                                        const margin = formatMarginCell(product);
                                        const isFlashing = flashingRowIds.has(product.id);
                                        return (
                                            <tr
                                                key={product.id}
                                                className={isFlashing ? 'catalog-row-flash' : undefined}
                                            >
                                                <td className="align-middle font-mono text-xs text-slate-600 dark:text-slate-400 whitespace-nowrap">
                                                    {product.sku || '—'}
                                                </td>
                                                <td className="align-middle text-sm" title={product.title || undefined}>
                                                    <span className="block truncate font-medium text-slate-900 dark:text-slate-100 max-w-[18rem]">
                                                        {product.title || '—'}
                                                    </span>
                                                </td>
                                                <td className="text-slate-600 dark:text-slate-400 align-middle text-sm">{product.vendor_name || product.vendor || '—'}</td>
                                                <td className="text-center align-middle">
                                                    {product.vendor_url ? (
                                                        <a href={product.vendor_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-accent-600 dark:text-accent-400 hover:underline text-xs">
                                                            <ExternalLink className="h-3.5 w-3.5" /> Link
                                                        </a>
                                                    ) : (
                                                        '—'
                                                    )}
                                                </td>
                                                <td className="text-right font-mono text-sm align-middle whitespace-nowrap text-slate-600 dark:text-slate-400">
                                                    {formatPrice(product.vendor_price)}
                                                </td>
                                                <td className="text-right font-mono text-sm align-middle whitespace-nowrap">{formatPrice(product.store_price)}</td>
                                                <td className="text-right font-mono text-sm align-middle">{product.store_stock ?? '—'}</td>
                                                <td className="text-center align-middle whitespace-nowrap">
                                                    <Badge variant={syncStatusVariant[status] || syncStatusVariant.pending}>
                                                        {syncStatusLabel[status] || syncStatusLabel.pending}
                                                    </Badge>
                                                </td>
                                                <td className="text-right text-sm text-slate-600 dark:text-slate-400 align-middle whitespace-nowrap">{margin}</td>
                                                <td className="text-slate-500 dark:text-slate-400 text-xs align-middle whitespace-nowrap">{formatLastStatus(product)}</td>
                                                <td className="text-right align-middle">
                                                    {status === 'needs_attention' ? (
                                                        <button
                                                            onClick={() => handleResetSyncStatus(product)}
                                                            disabled={resettingId === product.id}
                                                            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-accent-600 dark:text-accent-400 hover:bg-accent-50 dark:hover:bg-accent-900/20 transition"
                                                        >
                                                            <RotateCcw className={`h-3.5 w-3.5 ${resettingId === product.id ? 'animate-spin' : ''}`} />
                                                            Resync
                                                        </button>
                                                    ) : (
                                                        '—'
                                                    )}
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        )}
                    </div>

                    {filteredProducts.length > PRODUCTS_PER_PAGE && (
                        <div className="flex items-center justify-between border-t border-slate-200 dark:border-slate-700 px-4 py-3">
                            <p className="text-sm text-slate-500 dark:text-slate-400">
                                Showing <span className="font-medium text-slate-700 dark:text-slate-300">{(safePage - 1) * PRODUCTS_PER_PAGE + 1}</span>–<span className="font-medium text-slate-700 dark:text-slate-300">{Math.min(safePage * PRODUCTS_PER_PAGE, filteredProducts.length)}</span> of{' '}
                                <span className="font-medium text-slate-700 dark:text-slate-300">{filteredProducts.length}</span> products
                            </p>
                            <div className="flex items-center gap-1">
                                <button
                                    type="button"
                                    onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                                    disabled={safePage <= 1}
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
                                                onClick={() => setCurrentPage(pg)}
                                                className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
                                                    pg === safePage
                                                        ? 'bg-accent-600 text-white dark:bg-accent-500'
                                                        : 'border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700'
                                                }`}
                                            >
                                                {pg}
                                            </button>
                                        )
                                    )}
                                <button
                                    type="button"
                                    onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                                    disabled={safePage >= totalPages}
                                    className="rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-1.5 text-sm font-medium text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700 transition disabled:opacity-40 disabled:cursor-not-allowed"
                                >
                                    Next
                                </button>
                            </div>
                        </div>
                    )}
                </div>
            )}

            {selectedStore && viewMode === 'logs' && (
                <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 overflow-hidden">
                    <div className="border-b border-slate-200 dark:border-slate-700 px-4 py-3 flex items-start gap-3">
                        <ScrollText className="h-5 w-5 text-accent-600 dark:text-accent-400 shrink-0 mt-0.5" />
                        <div>
                            <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Activity log</h2>
                            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                                Scrape and marketplace sync messages from the last 24 hours. Older entries are removed automatically.
                            </p>
                        </div>
                    </div>
                    <div className="max-h-[min(70vh,32rem)] overflow-y-auto">
                        {logsLoading ? (
                            <div className="flex justify-center py-16">
                                <div className="h-8 w-8 animate-spin rounded-full border-2 border-slate-200 dark:border-slate-700 border-t-accent-500" />
                            </div>
                        ) : activityLogs.length === 0 ? (
                            <EmptyState
                                icon={ScrollText}
                                title="No activity yet"
                                description="Run Start Scraping, Manual sync, or scheduled updates — events will appear here."
                            />
                        ) : (
                            <ul className="divide-y divide-slate-100 dark:divide-slate-700/80">
                                {activityLogs.map((log) => (
                                    <li key={log.id} className="px-4 py-3 hover:bg-slate-50/80 dark:hover:bg-slate-800/40">
                                        <div className="flex flex-wrap items-baseline justify-between gap-2">
                                            <span className="text-xs font-mono text-slate-500 dark:text-slate-400">
                                                {formatDate(log.created_at)}
                                            </span>
                                            <span className="text-[10px] uppercase tracking-wide text-slate-400 dark:text-slate-500">
                                                {log.action_type?.replace(/_/g, ' ') || 'event'}
                                            </span>
                                        </div>
                                        <p className="mt-1 text-sm text-slate-800 dark:text-slate-200 leading-snug">
                                            {log.message}
                                        </p>
                                        {log.user_email ? (
                                            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                                                User: {log.user_email}
                                            </p>
                                        ) : null}
                                    </li>
                                ))}
                            </ul>
                        )}
                    </div>
                </div>
            )}

            {/* Store list & marketplace filter — only on All stores; switching stores uses the toolbar All stores button */}
            {!selectedStore && (
            <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-5">
                <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                    <div className="flex items-center gap-3">
                        <h2 className="text-section font-medium text-slate-900 dark:text-slate-100">
                            Stores
                        </h2>
                        <Link
                            to="/store-settings"
                            className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-1.5 text-sm font-medium text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700 transition"
                        >
                            <Settings className="h-3.5 w-3.5" />
                            Manage
                        </Link>
                    </div>
                    <Select
                        options={[
                            { value: '', label: 'All marketplaces' },
                            ...marketplaces.map((m) => ({ value: m.id, label: m.name })),
                        ]}
                        value={selectedMarketplace}
                        onChange={(e) => setSelectedMarketplace(e.target.value)}
                        className="min-w-[180px]"
                    />
                </div>

                {loading ? (
                    <p className="py-8 text-center text-sm text-slate-500 dark:text-slate-400">Loading stores…</p>
                ) : storeList.length === 0 ? (
                    <p className="py-8 text-center text-sm text-slate-500 dark:text-slate-400">
                        No stores. Create a store in Store Settings.
                    </p>
                ) : (
                    <div className="mt-4 grid grid-cols-1 gap-2">
                        {storeList.map((s) => (
                            <div
                                key={s.id}
                                className={`flex w-full items-center justify-between gap-3 rounded-lg border p-4 transition cursor-pointer ${
                                    selectedStore === s.id
                                        ? 'border-accent-500 dark:border-accent-500 bg-accent-50/50 dark:bg-accent-900/20 ring-1 ring-accent-500/20'
                                        : 'border-slate-200 dark:border-slate-700 bg-slate-50/50 dark:bg-slate-800/50 hover:border-slate-300 dark:hover:border-slate-600'
                                }`}
                            >
                                <button
                                    type="button"
                                    onClick={() => {
                                        setSelectedStore(s.id);
                                        setViewMode('history');
                                    }}
                                    className="flex min-w-0 flex-1 items-center gap-3 text-left"
                                >
                                    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-slate-200 dark:bg-slate-700">
                                        <Store className="h-4 w-4 text-slate-600 dark:text-slate-400" />
                                    </div>
                                    <div className="min-w-0">
                                        <p className="font-medium text-slate-900 dark:text-slate-100 truncate">{s.name}</p>
                                        <p className="text-xs text-slate-500 dark:text-slate-400">
                                            {s.product_count} product{s.product_count !== 1 ? 's' : ''}
                                            {s.marketplace_name && ` · ${s.marketplace_name}`}
                                            {s.schedule_active === false && (
                                                <span className="text-amber-600 dark:text-amber-400"> · Schedule off</span>
                                            )}
                                        </p>
                                    </div>
                                </button>
                                <button
                                    type="button"
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        setDeleteStoreConfirm(s);
                                    }}
                                    disabled={deletingStoreId === s.id}
                                    className="shrink-0 rounded-md p-2 text-slate-400 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-900/20 dark:hover:text-rose-400 transition"
                                    title="Delete store"
                                >
                                    <Trash2 className="h-4 w-4" />
                                </button>
                            </div>
                        ))}
                    </div>
                )}
            </div>
            )}

            <UpdateWithFileModal
                open={uploadModalOpen && !!selectedStore}
                onClose={() => setUploadModalOpen(false)}
                onUpload={handleUpload}
                storeName={selectedStoreData?.name}
                storeMarketplace={selectedStoreData?.marketplace_name}
                storeId={selectedStore}
                downloadSample={() =>
                    downloadSampleTemplate(selectedStore, resolveMarketplaceTemplateKind(selectedStoreData)).catch(() =>
                        setMessage('Failed to download template'),
                    )
                }
                loading={uploading}
                file={modalFile}
                setFile={setModalFile}
                template={modalTemplate}
                setTemplate={setModalTemplate}
            />

            <ConfirmModal
                open={!!deleteStoreConfirm}
                title="Delete store"
                message={`Delete "${deleteStoreConfirm?.name}"? All products and settings will be removed. This cannot be undone.`}
                confirmLabel="Delete"
                variant="danger"
                loading={deletingStoreId === deleteStoreConfirm?.id}
                onConfirm={() => handleDeleteStore(deleteStoreConfirm)}
                onCancel={() => setDeleteStoreConfirm(null)}
            />
            <ConfirmModal
                open={!!deleteUploadConfirm}
                title="Delete upload"
                message={`Delete this uploaded file? All products and mappings created from it will be removed. This cannot be undone.`}
                confirmLabel="Delete"
                variant="danger"
                loading={deletingUploadId === deleteUploadConfirm?.id}
                onConfirm={() => handleDeleteUpload(deleteUploadConfirm)}
                onCancel={() => setDeleteUploadConfirm(null)}
            />
            <ConfirmModal
                open={criticalModalOpen}
                title="Critical action"
                message="If you click Yes, all listing inventory for this store will be set to 0 on the marketplace (where possible), local stock will be cleared, and this store will be deactivated including its scheduled sync toggle. Only use this if something went wrong and you need an immediate stop."
                confirmLabel="Yes, zero inventory and deactivate"
                cancelLabel="Cancel"
                variant="danger"
                loading={criticalLoading}
                onConfirm={handleCriticalZeroConfirm}
                onCancel={() => !criticalLoading && setCriticalModalOpen(false)}
            />
        </div>
    );
}
