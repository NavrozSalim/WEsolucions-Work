import { useState, useEffect, useRef, useCallback, useLayoutEffect } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import {
    Search,
    UploadCloud,
    RefreshCw,
    Trash2,
    ChevronLeft,
    ChevronDown,
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
    resetAllCatalogListingsPending,
    getCatalogActivityLogs,
    getScrapeProgress,
    cancelCatalogScrape,
    updateProductMapping,
    downloadMydealTemplates,
} from '../../services/catalogService';
import MydealUploadModal from '../../components/catalog/MydealUploadModal';
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

/** Prefer server detail for errors; keep wording plain for non-technical users. */
function isMydealMarketplace(storeData) {
    const code = (storeData?.marketplace_code || storeData?.marketplace_name || '')
        .toString()
        .trim()
        .toLowerCase();
    return code === 'mydeal';
}

function formatCatalogError(err) {
    const status = err.response?.status;
    const d = err.response?.data;
    if (typeof d === 'string' && d.trim()) return d;
    if (d?.detail != null) {
        if (typeof d.detail === 'string') return d.detail;
        if (Array.isArray(d.detail)) return d.detail.map((x) => x?.msg || x).filter(Boolean).join('; ') || 'Something went wrong. Please try again.';
        return String(d.detail);
    }
    if (d?.error && typeof d.error === 'string') return d.error;
    if (d?.message && typeof d.message === 'string') return d.message;
    if (status === 500) return 'Something went wrong on our side. Please try again in a moment.';
    if (err.code === 'ECONNABORTED') return 'The request took too long. Check your connection and try again.';
    return err.message || 'Something went wrong. Please try again.';
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
            label: 'Sync catalog',
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

/** Compact numeric display for Pack QTY / Prep Fees / Shipping Fees columns.
 *  Uses up to 2 decimal places and strips trailing zeros so "1.0000" shows as
 *  "1" and "0.5000" shows as "0.5". Returns em-dash for null/empty/NaN.
 */
function formatNumericCell(val) {
    if (val == null || val === '') return '—';
    const n = parseFloat(val);
    if (isNaN(n)) return '—';
    return n.toFixed(2).replace(/\.?0+$/, '');
}

/** Inline-editable numeric cell used by the Pack / Prep / Ship columns.
 *  Click the value → turns into an input; Enter or blur submits, Esc cancels.
 *  ``onSave`` is fired with the new numeric value (or ``null`` on empty) and
 *  must return a Promise so we can show a spinner + revert on failure.
 */
function EditableNumericCell({ value, onSave, disabled = false, fieldLabel = 'value' }) {
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState('');
    const [saving, setSaving] = useState(false);
    const inputRef = useRef(null);

    useEffect(() => {
        if (editing && inputRef.current) {
            inputRef.current.focus();
            inputRef.current.select();
        }
    }, [editing]);

    const startEditing = () => {
        if (disabled || saving) return;
        setDraft(value == null ? '' : String(value));
        setEditing(true);
    };

    const commit = async () => {
        const trimmed = draft.trim();
        const parsed = trimmed === '' ? null : Number(trimmed);
        if (trimmed !== '' && (isNaN(parsed) || parsed < 0)) {
            setEditing(false);
            return;
        }
        const current = value == null ? null : Number(value);
        if (current === parsed || (current == null && parsed == null)) {
            setEditing(false);
            return;
        }
        setSaving(true);
        try {
            await onSave(parsed);
            setEditing(false);
        } catch {
            // keep the editor open on failure so the user can fix or cancel
        } finally {
            setSaving(false);
        }
    };

    if (editing) {
        return (
            <input
                ref={inputRef}
                type="number"
                step="0.01"
                min="0"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onBlur={commit}
                onKeyDown={(e) => {
                    if (e.key === 'Enter') commit();
                    else if (e.key === 'Escape') setEditing(false);
                }}
                disabled={saving}
                className="w-20 rounded border border-slate-300 bg-white px-1.5 py-0.5 text-right text-xs font-mono text-slate-900 focus:border-accent-500 focus:outline-none focus:ring-1 focus:ring-accent-500 disabled:opacity-60 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
                aria-label={`Edit ${fieldLabel}`}
            />
        );
    }

    return (
        <button
            type="button"
            onClick={startEditing}
            disabled={disabled}
            title={disabled ? undefined : `Click to edit ${fieldLabel}`}
            className={`inline-block min-w-[3rem] rounded px-1.5 py-0.5 text-right font-mono text-xs tabular-nums ${
                value == null
                    ? 'text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-900/20'
                    : 'text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800'
            } ${disabled ? 'cursor-not-allowed opacity-60' : 'cursor-text'} ${saving ? 'animate-pulse' : ''}`}
        >
            {formatNumericCell(value)}
        </button>
    );
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
 * Rows still waiting for a scrape pass (``sync_status='pending'`` only).
 * ``pending`` on the vendor payload also counts failed/needs_attention for
 * display; use this when deciding whether a scrape is still running.
 */
function vendorSyncPending(vendor) {
    if (!vendor) return 0;
    if (typeof vendor.sync_pending === 'number') return vendor.sync_pending;
    return (vendor.by_status?.pending) || 0;
}

/** True when a desktop/server ingest job is actively running (not merely failed rows). */
function vendorIngestIsRunning(vendor) {
    if (!vendor) return false;
    const job = vendorIngestJob(vendor);
    const jobStatus = job?.status;
    if (jobStatus === 'pending') return true;
    if (jobStatus === 'claimed') {
        const runner = vendor.runner || 'desktop';
        if (runner === 'server') {
            return vendorSyncPending(vendor) > 0;
        }
        return true;
    }
    return false;
}

/**
 * Pick the vendor that should drive the Scrape button label and tracking
 * state when a store has products from multiple desktop-runner vendors.
 * Prefer one that still has pending work; otherwise fall back to the first.
 */
function getActiveVendor(vendors) {
    if (!Array.isArray(vendors) || vendors.length === 0) return null;
    return vendors.find((v) => vendorSyncPending(v) > 0 || vendorIngestIsRunning(v)) || vendors[0];
}

/** HebScrapeJob desktop queue — not used for ``runner: live`` (Amazon/eBay server scrapes). */
function vendorIngestJob(vendor) {
    if (!vendor || vendor.runner === 'live') return null;
    return vendor.job || null;
}

function vendorIngestQueue(vendor) {
    if (!vendor || vendor.runner === 'live') return null;
    return vendor.queue || null;
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
        ? 'your computer'
        : runner === 'server'
            ? 'data feed'
            : 'live price check';
    const pct = Math.max(0, Math.min(100, Number(vendor.pct || 0)));
    const recent5 = vendor.ingested_last_5m || 0;
    const recent24 = vendor.ingested_last_24h || 0;
    const lastAgo = formatLastSync(vendor.last_ingest_at);
    const barColor = pct >= 100
        ? 'bg-emerald-500'
        : recent5 > 0
            ? 'bg-accent-500'
            : 'bg-amber-500';

    const job = vendorIngestJob(vendor);
    const queue = vendorIngestQueue(vendor);
    const jobStatus = job?.status;
    const isPending = jobStatus === 'pending';
    const isClaimed = jobStatus === 'claimed' && vendorIngestIsRunning(vendor);
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
                Pending — {parts.join(' · ')}. This will start on its own when it is your turn.
            </p>
        );
    } else if (isClaimed) {
        queueLine = (
            <p className="text-xs text-accent-600 dark:text-accent-300 mt-0.5">
                Running now — prices will appear here as your upload finishes. You can leave this page open or come back later.
            </p>
        );
    }

    return (
        <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-4 mb-4 shadow-sm">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-center gap-3">
                    <span className={`inline-flex h-2.5 w-2.5 rounded-full ${isActive ? 'bg-emerald-500 animate-pulse' : recent5 > 0 ? 'bg-emerald-500' : 'bg-slate-300 dark:bg-slate-600'}`} />
                    <div>
                        <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                            {label}
                            {' '}
                            {runnerLabel}
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

/**
 * Server-side catalog scrape for the **current store only** (`state.store_id` must match).
 */
function ServerCeleryScrapeStrip({ state, progressStoreId, selectedStoreId }) {
    if (!state?.active || !selectedStoreId) return null;
    if (progressStoreId && progressStoreId !== selectedStoreId) return null;
    if (state.store_id && state.store_id !== selectedStoreId) return null;

    // Don't treat phase "queued" as a separate UX: older APIs used it while the job was still
    // in flight (before worker started), which looked stuck. New API uses "running" when active.
    const isQueued = false;
    const headline = isQueued
        ? 'Job is in queue, please wait...'
        : 'Live vendor scrape (server) running';
    const pillClass = isQueued ? 'bg-amber-200 text-amber-950 dark:bg-amber-900/40 dark:text-amber-100' : 'bg-sky-200 text-sky-900 dark:bg-sky-800 dark:text-sky-200';

    return (
        <div className="rounded-lg border border-sky-200 dark:border-sky-800 bg-sky-50/80 dark:bg-sky-950/30 p-4 mb-4 shadow-sm">
            <div className="flex items-start gap-3">
                <span className={`mt-0.5 inline-flex h-2.5 w-2.5 shrink-0 rounded-full ${isQueued ? 'bg-amber-500' : 'bg-sky-500 animate-pulse'}`} />
                <div>
                    <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                        {headline}
                        <span className={`ml-2 inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${pillClass}`}>
                            {isQueued ? 'queued' : 'running'}
                        </span>
                    </h3>
                    {!isQueued && (
                        <p className="text-xs text-slate-600 dark:text-slate-300 mt-0.5">
                            You can leave this page; product rows update as each item finishes. Use “Stop Scraping” to cancel.
                        </p>
                    )}
                </div>
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
    /** True while the active products page request is in flight. */
    const [productsFetching, setProductsFetching] = useState(false);
    const [uploading, setUploading] = useState(false);
    const [syncing, setSyncing] = useState(false);
    const [flowStatus, setFlowStatus] = useState(''); // file uploaded | ready to sync | syncing | success | failed
    const [message, setMessage] = useState('');
    const [uploadModalOpen, setUploadModalOpen] = useState(false);
    const [mydealUploadOpen, setMydealUploadOpen] = useState(false);
    const [mydealDownloadOpen, setMydealDownloadOpen] = useState(false);
    const [mydealDownloading, setMydealDownloading] = useState(false);
    const [deleteStoreConfirm, setDeleteStoreConfirm] = useState(null);
    const [deletingStoreId, setDeletingStoreId] = useState(null);
    const [resettingId, setResettingId] = useState(null);
    const [syncingUploadId, setSyncingUploadId] = useState(null);
    const [replaceCatalogOnSync, setReplaceCatalogOnSync] = useState(false);
    const [scraping, setScraping] = useState(false);
    const [scrapingUploadId, setScrapingUploadId] = useState(null);
    const [deleteUploadConfirm, setDeleteUploadConfirm] = useState(null);
    const [deletingUploadId, setDeletingUploadId] = useState(null);
    const [modalFile, setModalFile] = useState(null);
    const [modalTemplate, setModalTemplate] = useState('standard');
    const [progress, setProgress] = useState(0);
    const progressRef = useRef(null);
    const [currentPage, setCurrentPage] = useState(1);
    const PRODUCTS_PER_PAGE = 25;
    const [totalProductCount, setTotalProductCount] = useState(0);
    /** Bumped after catalog sync so the Products tab refetches (even if you stayed on this tab). */
    const [catalogMutationNonce, setCatalogMutationNonce] = useState(0);
    const [debouncedSearch, setDebouncedSearch] = useState('');
    const productsFetchGenRef = useRef(0);
    /** When store / search / status filter change, clear rows; page-only changes keep prior rows (stale-while-revalidate). */
    const productsListBaseKeyRef = useRef('');
    /** Increments each merged live poll (used to throttle store-list refetch). */
    const livePollBurstRef = useRef(0);
    const [exportScope, setExportScope] = useState('all');
    const [exportDownloading, setExportDownloading] = useState(false);
    const [manualPushLoading, setManualPushLoading] = useState(false);
    const [criticalModalOpen, setCriticalModalOpen] = useState(false);
    const [criticalLoading, setCriticalLoading] = useState(false);
    const [resetPendingModalOpen, setResetPendingModalOpen] = useState(false);
    const [resetPendingLoading, setResetPendingLoading] = useState(false);
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
    const [trackingServerScrape, setTrackingServerScrape] = useState(false);
    const trackingServerScrapeRef = useRef(false);
    useEffect(() => { trackingServerScrapeRef.current = trackingServerScrape; }, [trackingServerScrape]);
    const [stoppingScrape, setStoppingScrape] = useState(false);
    // Row IDs whose sync_status just changed — used to flash the row yellow.
    const [flashingRowIds, setFlashingRowIds] = useState(() => new Set());
    const prevStatusRef = useRef(new Map()); // product.id -> last seen sync_status
    const flashTimersRef = useRef(new Map()); // product.id -> timeout id

    const { setSidebarActivity, clearSidebarActivity, clearCatalogActivities } = useSidebarActivity();

    const selectedStoreData = storeList.find((s) => s.id === selectedStore);
    const isMydealStore = isMydealMarketplace(selectedStoreData);
    // Only show Pack QTY / Prep Fees / Shipping Fees columns when the store
    // has at least one ``margin_type='fixed'`` tier configured. The backend
    // (CatalogStoresView) exposes this via ``has_fixed_tier`` so we don't
    // need a separate round-trip to the pricing-settings endpoint.
    const showFixedColumns = Boolean(selectedStoreData?.has_fixed_tier);

    useEffect(
        () => () => {
            clearCatalogActivities();
        },
        [clearCatalogActivities],
    );

    // Re-fetch the current product page with the active filters. Used after
    // mutations (reset status, scrape, manual push, critical action, inline
    // edits) so the visible rows reflect server state without loading every
    // product across every page.
    const refreshProducts = useCallback(() => {
        if (!selectedStore || viewMode !== 'products') return;
        productsFetchGenRef.current += 1;
        const gen = productsFetchGenRef.current;
        getProducts(selectedStore, {
            page: currentPage,
            pageSize: PRODUCTS_PER_PAGE,
            q: debouncedSearch,
            status: statusFilter,
        })
            .then((res) => {
                if (gen !== productsFetchGenRef.current) return;
                setProducts(Array.isArray(res.data) ? res.data : []);
                setTotalProductCount(Number.isFinite(res.count) ? res.count : 0);
            })
            .catch(() => { /* silent — next scheduled re-fetch will retry */ });
    }, [selectedStore, viewMode, currentPage, debouncedSearch, statusFilter]);

    const refreshLiveData = useCallback((opts = {}) => {
        if (!selectedStore) return;
        const skipStores = Boolean(opts.skipStores);
        if (!skipStores) {
            getCatalogStores(selectedMarketplace || null).then((r) => setStoreList(Array.isArray(r.data) ? r.data : []));
        }
        getCatalogUploads(selectedStore)
            .then((r) => setUploads(Array.isArray(r.data) ? r.data : []))
            .catch(() => {});
        if (viewMode === 'products') {
            refreshProducts();
        } else if (viewMode === 'logs') {
            getCatalogActivityLogs(selectedStore).then((r) =>
                setActivityLogs(Array.isArray(r.data) ? r.data : []));
        }
    }, [selectedStore, selectedMarketplace, viewMode, refreshProducts]);

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

    // Debounce the search box so typing doesn't fire a request per keystroke.
    useEffect(() => {
        const t = setTimeout(() => setDebouncedSearch(search.trim()), 250);
        return () => clearTimeout(t);
    }, [search]);

    // Reset to page 1 whenever the filter criteria or store change. Without
    // this, switching from "all" to a narrow filter could leave ``currentPage``
    // pointing beyond the new result set and flash an empty table.
    useEffect(() => {
        setCurrentPage(1);
    }, [debouncedSearch, statusFilter, selectedStore]);

    // Fetch one page of products from the server — this is the hot path
    // that used to take 40+ seconds because the old code looped through
    // every page up to 40,000 items before rendering. Now we fetch exactly
    // PRODUCTS_PER_PAGE rows, push search / status filters down to SQL,
    // and show the page as soon as it arrives.
    useEffect(() => {
        if (!selectedStore || viewMode !== 'products') return undefined;
        const listBaseKey = `${selectedStore}|${debouncedSearch}|${statusFilter}`;
        if (productsListBaseKeyRef.current !== listBaseKey) {
            productsListBaseKeyRef.current = listBaseKey;
            setProducts([]);
            setTotalProductCount(0);
        }

        const gen = ++productsFetchGenRef.current;
        const ac = new AbortController();
        setProductsFetching(true);
        getProducts(selectedStore, {
            page: currentPage,
            pageSize: PRODUCTS_PER_PAGE,
            q: debouncedSearch,
            status: statusFilter,
            signal: ac.signal,
        })
            .then((res) => {
                if (gen !== productsFetchGenRef.current) return;
                setProducts(Array.isArray(res.data) ? res.data : []);
                setTotalProductCount(Number.isFinite(res.count) ? res.count : 0);
            })
            .catch((err) => {
                if (ac.signal.aborted) return;
                if (gen !== productsFetchGenRef.current) return;
                setProducts([]);
                setTotalProductCount(0);
                setMessage(formatCatalogError(err));
            })
            .finally(() => {
                if (gen === productsFetchGenRef.current) setProductsFetching(false);
            });
        return () => ac.abort();
    }, [selectedStore, viewMode, currentPage, debouncedSearch, statusFilter, catalogMutationNonce]);

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
        const bootstrapping =
            productsFetching
            && selectedStore
            && viewMode === 'products'
            && products.length === 0;
        if (bootstrapping) {
            setSidebarActivity('catalog-products', {
                title: 'Product listings',
                description: 'Loading the first page from the server…',
            });
        } else {
            clearSidebarActivity('catalog-products');
        }
    }, [productsFetching, products.length, selectedStore, viewMode, setSidebarActivity, clearSidebarActivity]);

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
        // Single interval: scrape progress + catalog data. Wider spacing and throttled
        // store-list refetch reduce UI/API load while workers are busy.
        const needsLivePolling = activeFlow || inGraceWindow || trackingScrape || trackingServerScrape;
        if (!needsLivePolling) return undefined;

        let cancelled = false;
        let graceTimeoutId = null;

        const poll = () => {
            if (cancelled) return;
            const storeId = selectedStore;
            livePollBurstRef.current += 1;
            const burst = livePollBurstRef.current;
            getScrapeProgress(storeId)
                .then((res) => {
                    if (cancelled) return;
                    const data = res.data || null;
                    if (data?.store_id && data.store_id !== storeId) return;
                    setScrapeProgress(data);
                })
                .catch(() => { /* transient — retry next tick */ });
            refreshLiveData({ skipStores: burst % 4 !== 0 });
        };

        poll();
        const intervalId = setInterval(poll, 20000);
        if (!activeFlow && !trackingScrape && !trackingServerScrape && inGraceWindow) {
            graceTimeoutId = setTimeout(() => {
                cancelled = true;
                clearInterval(intervalId);
            }, Math.max(0, liveRefreshUntil - Date.now()));
        }

        return () => {
            cancelled = true;
            clearInterval(intervalId);
            if (graceTimeoutId) clearTimeout(graceTimeoutId);
        };
    }, [selectedStore, flowStatus, liveRefreshUntil, trackingScrape, trackingServerScrape, refreshLiveData]);

    // One scrape-progress fetch when opening Products; clear stale payload from other stores first.
    useEffect(() => {
        if (!selectedStore || viewMode !== 'products') {
            setScrapeProgress(null);
            return undefined;
        }
        setScrapeProgress(null);
        let cancelled = false;
        getScrapeProgress(selectedStore)
            .then((res) => {
                if (cancelled) return;
                const data = res.data || null;
                if (data?.store_id && data.store_id !== selectedStore) return;
                setScrapeProgress(data);
            })
            .catch(() => { /* ignore */ });
        return () => { cancelled = true; };
    }, [selectedStore, viewMode]);

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
        const jobStatus = vendorIngestJob(active)?.status;
        if (jobStatus === 'cancelled') {
            setTrackingScrape(false);
            setFlowStatus('');
            setMessage(`${activeLabel} price check was cancelled.`);
            return;
        }
        if (jobStatus === 'failed') {
            setTrackingScrape(false);
            setFlowStatus('failed');
            setMessage(`${activeLabel} price check failed on your computer.`);
            return;
        }
        if (jobStatus === 'done') {
            setTrackingScrape(false);
            setFlowStatus('success');
            return;
        }
        const allDone = vendors.every(
            (v) => vendorSyncPending(v) === 0 && !vendorIngestIsRunning(v) && (v.total || 0) > 0,
        );
        if (allDone) {
            setTrackingScrape(false);
            setFlowStatus('success');
            const parts = vendors.map(
                (v) => `${v.total} ${v.label || v.code.toUpperCase()}`,
            );
            setMessage(
                `Done. ${parts.join(' + ')} product(s) now have prices from your computer.`,
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
                refreshProducts();
            })
            .catch((err) => setMessage(formatCatalogError(err) || 'Reset failed'))
            .finally(() => setResettingId(null));
    };

    // Inline edit for Pack QTY / Prep Fees / Shipping Fees. Optimistically
    // updates the row, then persists via PATCH. On failure we re-throw so the
    // cell editor can revert its saving state.
    const handleUpdateProductField = useCallback(
        (product, field, value) => {
            if (!selectedStore) return Promise.reject(new Error('No store selected'));
            const prevValue = product[field];
            setProducts((list) =>
                list.map((p) => (p.id === product.id ? { ...p, [field]: value } : p)),
            );
            return updateProductMapping(selectedStore, product.id, { [field]: value })
                .then((res) => {
                    const updated = res?.data;
                    if (updated && typeof updated === 'object') {
                        setProducts((list) =>
                            list.map((p) => (p.id === product.id ? { ...p, ...updated } : p)),
                        );
                    }
                })
                .catch((err) => {
                    setProducts((list) =>
                        list.map((p) => (p.id === product.id ? { ...p, [field]: prevValue } : p)),
                    );
                    setMessage(formatCatalogError(err) || `Failed to update ${field}`);
                    throw err;
                });
        },
        [selectedStore],
    );

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
        }, 1800);
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
        setFlowStatus('');
        setMessage('Uploading catalog…');
        uploadCatalog(file, selectedStore)
            .then((res) => {
                setUploadModalOpen(false);
                setModalFile(null);
                setModalTemplate('standard');
                return getCatalogUploads(selectedStore).then((r) => {
                    setUploads(Array.isArray(r.data) ? r.data : []);
                    return res;
                });
            })
            .then((res) => {
                setFlowStatus('ready to sync');
                const isAsync = res?.status === 202 || res?.data?.status === 'ingesting';
                setMessage(
                    isAsync
                        ? (res?.data?.message
                            || 'File received. Rows are being processed in the background — wait until upload status is "validated" in Upload history, then use Ready to Upload.')
                        : 'Catalog uploaded. Open Upload history and click Ready to Upload to create products. '
                        + 'Vendor prices update when you click Start Scraping or when your schedule runs.',
                );
                getCatalogStores(selectedMarketplace || null).then((r) => setStoreList(Array.isArray(r.data) ? r.data : []));
            })
            .catch((err) => {
                setFlowStatus('failed');
                setMessage(formatCatalogError(err) || 'Upload failed');
            })
            .finally(() => setUploading(false));
    };

    const handleMydealDownload = (type) => {
        if (!selectedStore) return;
        setMydealDownloading(true);
        setMydealDownloadOpen(false);
        downloadMydealTemplates(selectedStore, type)
            .then((res) => {
                const profile = selectedStoreData?.name || selectedStoreData?.mydeal_templates?.store_name || 'Mydeal';
                const name = type === 'price'
                    ? `Mydeal - ${profile} - Price Template.csv`
                    : type === 'inventory'
                        ? `Mydeal - ${profile} - Inventory Template.csv`
                        : `Mydeal - ${profile} - Templates.zip`;
                const blob = new Blob([res.data], {
                    type: type === 'both' ? 'application/zip' : 'text/csv',
                });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = name;
                a.click();
                URL.revokeObjectURL(url);
            })
            .catch((err) => setMessage(formatCatalogError(err) || 'Download failed'))
            .finally(() => setMydealDownloading(false));
    };

    const handleSync = (uploadId = null) => {
        if (!selectedStore) return;
        setSyncing(true);
        setFlowStatus('syncing');
        setSyncingUploadId(uploadId);
        setMessage('Updating your catalog and creating products… After this finishes, use Start Scraping to refresh vendor prices (or your schedule will run it).');
        startProgress();

        const MAX_RETRIES = 3;
        let attempt = 0;

        const runSync = () => {
            attempt++;
            return triggerCatalogSync(selectedStore, false, uploadId, {
                autoScrape: false,
                replaceStoreCatalog: replaceCatalogOnSync,
            })
                .then((res) => {
                const added = res?.data?.added ?? 0;
                const updated = res?.data?.updated ?? 0;
                const replaceDeactivated = res?.data?.replace_deactivated ?? 0;
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
                const parts = [];
                if (replaceDeactivated > 0) {
                    parts.push(`${replaceDeactivated} previous listing(s) deactivated (replace catalog)`);
                }
                if (added > 0) parts.push(`${added} new listing(s) added to your store`);
                if (updated > 0) {
                    parts.push(`${updated} listing(s) in your store updated from this file`);
                }
                const summary = parts.length > 0 ? `Sync complete. ${parts.join('; ')}.` : 'Sync complete.';
                setMessage(
                    `${summary}${scrapeMsg} `
                    + 'Counts apply only to this store and your account — not another user’s catalog. '
                    + 'The product list shows active listings for this store. '
                    + 'Use Start Scraping when price columns show “—”.',
                );
                getCatalogUploads(selectedStore).then((r) => setUploads(Array.isArray(r.data) ? r.data : []));
                getCatalogStores(selectedMarketplace || null).then((r) => setStoreList(Array.isArray(r.data) ? r.data : []));
                setSearch('');
                setDebouncedSearch('');
                setStatusFilter('');
                setCurrentPage(1);
                setCatalogMutationNonce((n) => n + 1);
                })
                .catch((err) => {
                    const isNetErr = !err.response || err.code === 'ERR_NETWORK' || err.message === 'Network Error';
                    if (isNetErr && attempt < MAX_RETRIES) {
                        setMessage(`Connection issue — trying again (${attempt + 1} of ${MAX_RETRIES})… Your sync is still running in the background.`);
                        return new Promise((r) => setTimeout(r, 5000)).then(runSync);
                    }
                    finishProgress(false);
                    if (isNetErr) {
                        setFlowStatus('syncing');
                        setMessage(
                            'Your sync is still running. Refresh this page in a minute to see the latest results.',
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
        const storeId = selectedStore;
        setScraping(true);
        setScrapingUploadId(uploadId);
        setFlowStatus('scraping');
        setMessage('Fetching vendor prices and stock…');
        startProgress();

        const MAX_RETRIES = 1;
        let attempt = 0;

        const finishKickoffAndSyncProgress = (ok, proc) => {
            finishProgress(true);
            return getScrapeProgress(storeId)
                .then((p) => {
                    const progress = p?.data || null;
                    if (progress?.store_id && progress.store_id !== storeId) return;
                    setScrapeProgress(progress);
                    const serverOn = Boolean(progress?.server_celery_scrape?.active);
                    if (serverOn) {
                        trackingServerScrapeRef.current = true;
                        setTrackingServerScrape(true);
                    } else {
                        trackingServerScrapeRef.current = false;
                        setTrackingServerScrape(false);
                    }
                    const vendors = getVendorSummaries(progress);
                    const pendingVendors = vendors.filter(
                        (v) => vendorSyncPending(v) > 0 || vendorIngestIsRunning(v),
                    );
                    const shouldTrack = !uploadId && pendingVendors.length > 0;
                    if (shouldTrack) {
                        setTrackingScrape(true);
                        setFlowStatus('scraping');
                        const parts = pendingVendors.map((v) => {
                            const lbl = v.label || (v.code || '').toUpperCase();
                            return `${lbl}: ${v.scraped || 0}/${v.total || 0} populated (${vendorSyncPending(v)} remaining)`;
                        });
                        if (serverOn) {
                            setMessage(
                                `${parts.join(' · ')} (your computer) — we are also fetching prices on our servers. Watch the blue bar above.`,
                            );
                        } else {
                            setMessage(
                                `${parts.join(' · ')} — waiting for your computer to finish uploading. This updates as it goes.`,
                            );
                        }
                    } else if (serverOn) {
                        setFlowStatus('scraping');
                        setMessage(
                            'We are fetching vendor prices on our servers. You can leave this page; the blue bar and table update as we go.',
                        );
                    } else {
                        setFlowStatus('success');
                        setMessage(
                            `Done. ${ok} of ${proc} product(s) now have the latest vendor price and stock. `
                            + 'If you use a marketplace, updates there may follow shortly.',
                        );
                    }
                })
                .catch(() => {
                    setFlowStatus('success');
                    setMessage(
                        `Done. ${ok} of ${proc} product(s) now have the latest vendor price and stock. `
                        + 'If you use a marketplace, updates there may follow shortly.',
                    );
                })
                .finally(() => {
                    getCatalogUploads(selectedStore).then((r) => setUploads(Array.isArray(r.data) ? r.data : []));
                    getCatalogStores(selectedMarketplace || null).then((r) => setStoreList(Array.isArray(r.data) ? r.data : []));
                    if (viewMode === 'products') {
                        refreshProducts();
                    }
                    if (viewMode === 'logs') {
                        getCatalogActivityLogs(selectedStore).then((r) =>
                            setActivityLogs(Array.isArray(r.data) ? r.data : []));
                    }
                });
        };

        const runScrape = () => {
            attempt++;
            return triggerCatalogScrape(storeId, false, uploadId)
                .then((res) => {
                    const ok = res?.data?.rows_succeeded ?? 0;
                    const proc = res?.data?.rows_processed ?? 0;
                    return finishKickoffAndSyncProgress(ok, proc);
                })
                .catch((err) => {
                    if (err.response?.status === 409) {
                        finishProgress(false);
                        setFlowStatus('');
                        const d = err.response?.data || {};
                        const detail = typeof d.detail === 'string' ? d.detail : '';
                        setMessage(
                            detail
                                || (d.error === 'catalog_scrape_already_running'
                                    ? 'A vendor scrape is already running for this store. Wait for it to finish or use Stop scraping.'
                                    : formatCatalogError(err)),
                        );
                        setTrackingScrape(false);
                        setTrackingServerScrape(false);
                        trackingScrapeRef.current = false;
                        trackingServerScrapeRef.current = false;
                        return Promise.resolve();
                    }
                    const isNetworkError = !err.response || err.code === 'ERR_NETWORK' || err.message === 'Network Error';
                    const scheduleRetry = () => {
                        setMessage(`Connection issue — trying again (${attempt + 1} of ${MAX_RETRIES})… Your price check is still running in the background.`);
                        return new Promise((resolve) => setTimeout(resolve, 5000)).then(runScrape);
                    };
                    if (isNetworkError && attempt < MAX_RETRIES) {
                        return getScrapeProgress(storeId)
                            .then((p) => {
                                const progress = p?.data || null;
                                if (progress?.store_id && progress.store_id !== storeId) {
                                    return scheduleRetry();
                                }
                                if (progress?.server_celery_scrape?.active) {
                                    setMessage(
                                        'Connection dropped after the server accepted your price check. '
                                        + 'Tracking progress here — avoid clicking Start again to prevent duplicate jobs.',
                                    );
                                    return finishKickoffAndSyncProgress(0, 0);
                                }
                                return scheduleRetry();
                            })
                            .catch(() => scheduleRetry());
                    }
                    finishProgress(false);
                    if (isNetworkError) {
                        setFlowStatus('scraping');
                        setMessage(
                            'Your price check may still be running. Refresh this page in a minute to see updated prices.',
                        );
                    } else {
                        setFlowStatus('failed');
                        setMessage(formatCatalogError(err));
                    }
                    return Promise.resolve();
                });
        };

        runScrape().finally(() => {
            // Keep `scraping` true while tracking desktop or server (Celery) scrape; effects clear it.
            if (!trackingScrapeRef.current && !trackingServerScrapeRef.current) {
                setScraping(false);
                setScrapingUploadId(null);
            }
        });
    };

    // When tracking ends (desktop or server), release the Scrape button.
    useEffect(() => {
        if (!trackingScrape && !trackingServerScrape) {
            setScraping(false);
            setScrapingUploadId(null);
        }
    }, [trackingScrape, trackingServerScrape]);

    // Sync server scrape tracking only for the selected store’s progress payload.
    useEffect(() => {
        if (!selectedStore) {
            setTrackingServerScrape(false);
            trackingServerScrapeRef.current = false;
            return;
        }
        if (!scrapeProgress) {
            setTrackingServerScrape(false);
            trackingServerScrapeRef.current = false;
            return;
        }
        if (scrapeProgress.store_id && scrapeProgress.store_id !== selectedStore) {
            setTrackingServerScrape(false);
            trackingServerScrapeRef.current = false;
            return;
        }
        const active = Boolean(scrapeProgress.server_celery_scrape?.active);
        setTrackingServerScrape(active);
        trackingServerScrapeRef.current = active;
    }, [scrapeProgress, selectedStore]);

    const handleStopScrape = () => {
        if (!selectedStore || stoppingScrape) return;
        setStoppingScrape(true);
        const activeVendor = getActiveVendor(getVendorSummaries(scrapeProgress));
        const vendorLabel = activeVendor?.label
            || (activeVendor?.code || 'desktop').toUpperCase();
        setMessage(`Stopping the ${vendorLabel} price check…`);
        cancelCatalogScrape(selectedStore)
            .then((res) => {
                const hebStopped = Array.isArray(res?.data?.cancelled) && res.data.cancelled.length > 0;
                const serverStopped = Boolean(res?.data?.server_scrape_stopped);
                setTrackingScrape(false);
                if (hebStopped || serverStopped) {
                    setTrackingServerScrape(false);
                    trackingServerScrapeRef.current = false;
                    if (serverStopped) {
                        const sid = selectedStore;
                        setScrapeProgress((prev) => {
                            if (!prev || (prev.store_id && prev.store_id !== sid)) return prev;
                            return {
                                ...prev,
                                server_celery_scrape: {
                                    ...(prev.server_celery_scrape || {}),
                                    active: false,
                                    phase: null,
                                    store_id: sid,
                                },
                            };
                        });
                    }
                }
                setFlowStatus(hebStopped || serverStopped ? 'success' : '');
                setMessage(
                    hebStopped || serverStopped
                        ? 'Stop was sent. Running price checks should wind down in a few seconds.'
                        : (res?.data?.detail || 'Nothing was running to stop.'),
                );
                getScrapeProgress(selectedStore)
                    .then((p) => {
                        const data = p.data || null;
                        if (data?.store_id && data.store_id !== selectedStore) return;
                        setScrapeProgress(data);
                    })
                    .catch(() => {});
            })
            .catch((err) => {
                setMessage(formatCatalogError(err) || 'Could not stop the price check.');
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
                    refreshProducts();
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

    const handleResetAllPendingConfirm = () => {
        if (!selectedStore) return;
        setResetPendingLoading(true);
        resetAllCatalogListingsPending(selectedStore)
            .then((res) => {
                const n = res.data?.listings_reset ?? 0;
                setResetPendingModalOpen(false);
                setFlowStatus('success');
                setMessage(`${n.toLocaleString()} listing${n === 1 ? '' : 's'} set to Pending. Run Start Scraping when you want fresh vendor prices.`);
                if (viewMode === 'products') {
                    refreshProducts();
                }
                getScrapeProgress(selectedStore)
                    .then((p) => {
                        const data = p.data || null;
                        if (data?.store_id && data.store_id !== selectedStore) return;
                        setScrapeProgress(data);
                    })
                    .catch(() => {});
                getCatalogActivityLogs(selectedStore).then((r) =>
                    setActivityLogs(Array.isArray(r.data) ? r.data : [])).catch(() => {});
            })
            .catch((err) => {
                setFlowStatus('failed');
                setMessage(formatCatalogError(err) || 'Could not reset listings.');
            })
            .finally(() => setResetPendingLoading(false));
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
                    refreshProducts();
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
                if (selectedStore) {
                    productsFetchGenRef.current += 1;
                    const gen = productsFetchGenRef.current;
                    getProducts(selectedStore, {
                        page: currentPage,
                        pageSize: PRODUCTS_PER_PAGE,
                        q: debouncedSearch,
                        status: statusFilter,
                    })
                        .then((res) => {
                            if (gen !== productsFetchGenRef.current) return;
                            setProducts(Array.isArray(res.data) ? res.data : []);
                            setTotalProductCount(Number.isFinite(res.count) ? res.count : 0);
                        })
                        .catch(() => { /* next navigation will refetch */ });
                }
            })
            .catch((err) => setMessage(formatCatalogError(err) || 'Failed to delete upload'))
            .finally(() => setDeletingUploadId(null));
    };

    // ``products`` already contains exactly one server page filtered by the
    // active search / status — no client-side slicing is needed. We keep
    // ``filteredProducts`` as an alias so the existing JSX below doesn't
    // need to be rewritten.
    const filteredProducts = products;
    const totalPages = Math.max(
        1,
        Math.ceil(totalProductCount / PRODUCTS_PER_PAGE),
    );
    const safePage = Math.min(Math.max(1, currentPage), totalPages);
    const paginatedProducts = filteredProducts;
    const hasActiveFilters = Boolean(debouncedSearch || statusFilter);

    const hasPendingUpload = uploads.some((u) => ['pending', 'validated'].includes(u.status));

    return (
        <div className="space-y-6">
            <PageHeader
                title="Catalog"
                description="Pick a store, upload a catalog file, use Ready to Upload (upload history) to create products, then Start Scraping or use your schedule for vendor prices."
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
                            {isMydealStore ? (
                                <>
                                    <Button
                                        variant="primary"
                                        size="sm"
                                        onClick={() => setMydealUploadOpen(true)}
                                        disabled={uploading}
                                    >
                                        <UploadCloud className="h-4 w-4 mr-1.5" />
                                        Upload / replace templates
                                    </Button>
                                    <Button
                                        variant="secondary"
                                        size="sm"
                                        onClick={() => setUploadModalOpen(true)}
                                        disabled={uploading}
                                    >
                                        Upload catalog
                                    </Button>
                                    <div className="relative">
                                        <Button
                                            variant="secondary"
                                            size="sm"
                                            onClick={() => setMydealDownloadOpen((o) => !o)}
                                            disabled={mydealDownloading}
                                        >
                                            <Download className="h-4 w-4 mr-1.5" />
                                            Download templates
                                            <ChevronDown className="h-3 w-3 ml-1" />
                                        </Button>
                                        {mydealDownloadOpen && (
                                            <div className="absolute right-0 top-full z-20 mt-1 min-w-[11rem] rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 py-1 shadow-lg">
                                                <button type="button" className="block w-full px-3 py-2 text-left text-xs hover:bg-slate-50 dark:hover:bg-slate-800" onClick={() => handleMydealDownload('price')}>Price CSV</button>
                                                <button type="button" className="block w-full px-3 py-2 text-left text-xs hover:bg-slate-50 dark:hover:bg-slate-800" onClick={() => handleMydealDownload('inventory')}>Inventory CSV</button>
                                                <button type="button" className="block w-full px-3 py-2 text-left text-xs hover:bg-slate-50 dark:hover:bg-slate-800" onClick={() => handleMydealDownload('both')}>Both (ZIP)</button>
                                            </div>
                                        )}
                                    </div>
                                </>
                            ) : (
                                <Button
                                    variant="primary"
                                    size="sm"
                                    onClick={() => setUploadModalOpen(true)}
                                    disabled={uploading}
                                >
                                    <UploadCloud className="h-4 w-4 mr-1.5" />
                                    Upload Catalog
                                </Button>
                            )}
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
                                description="Upload a catalog file with Upload Catalog in the bar above, then Ready to Upload below when it appears."
                                action={
                                    <Button
                                        variant="primary"
                                        size="sm"
                                        onClick={() => selectedStore && setUploadModalOpen(true)}
                                        disabled={!selectedStore || uploading}
                                    >
                                        <UploadCloud className="h-4 w-4 mr-1.5" />
                                        Upload Catalog
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
                                            <td className="text-right text-sm tabular-nums align-middle whitespace-nowrap">
                                                {u.status === 'processing' && (u.total_rows ?? 0) > 0
                                                    ? `${u.processed_rows} / ${u.total_rows}`
                                                    : (u.total_rows ?? '—')}
                                            </td>
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
                                Ready to Upload creates listings in your store from your file. Use Start Scraping (or your schedule) when you want vendor prices and stock.
                            </p>
                            <label className="flex items-start gap-2 text-xs text-slate-600 dark:text-slate-400 cursor-pointer">
                                <input
                                    type="checkbox"
                                    className="mt-0.5 rounded border-slate-300"
                                    checked={replaceCatalogOnSync}
                                    onChange={(e) => setReplaceCatalogOnSync(e.target.checked)}
                                    disabled={syncing}
                                />
                                <span>
                                    Replace store catalog — deactivate all current listings in this store before applying the file (use for a full re-import).
                                </span>
                            </label>
                            <Button variant="primary" onClick={handleSyncFromModal} disabled={syncing}>
                                <RefreshCw className={`h-4 w-4 mr-2 ${syncing ? 'animate-spin' : ''}`} />
                                Ready to Upload
                            </Button>
                        </div>
                    )}
                </div>
            )}

            {/* Server-side Celery catalog scrape (Amazon, eBay, …) — when active, poll keeps rows fresh */}
            {selectedStore && viewMode === 'products' && (
                <ServerCeleryScrapeStrip
                    state={scrapeProgress?.server_celery_scrape}
                    progressStoreId={scrapeProgress?.store_id}
                    selectedStoreId={selectedStore}
                />
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
                                {productsFetching && products.length === 0
                                    ? 'Loading…'
                                    : productsFetching && products.length > 0
                                        ? 'Updating page…'
                                        : hasActiveFilters
                                            ? `${totalProductCount.toLocaleString()} match${totalProductCount === 1 ? '' : 'es'}`
                                            : `${totalProductCount.toLocaleString()} product${totalProductCount === 1 ? '' : 's'}`}
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
                                <Button
                                    variant="secondary"
                                    size="sm"
                                    onClick={() => setResetPendingModalOpen(true)}
                                    disabled={
                                        resetPendingLoading
                                        || manualPushLoading
                                        || scraping
                                        || !selectedStore
                                    }
                                    title="Mark every active listing as Pending so the next scrape refreshes vendor data"
                                >
                                    <RotateCcw className={`h-4 w-4 mr-1.5 ${resetPendingLoading ? 'animate-spin' : ''}`} />
                                    Reset to pending
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
                                const activeJobStatus = vendorIngestJob(activeVendor)?.status;
                                const isPending = activeJobStatus === 'pending';
                                const isClaimed = activeJobStatus === 'claimed'
                                    && vendorIngestIsRunning(activeVendor);
                                const desktopRunnerBusy = isPending || isClaimed || trackingScrape;
                                const isActive = desktopRunnerBusy || trackingServerScrape;
                                const aheadCount = vendorIngestQueue(activeVendor)?.ahead_count || 0;
                                const etaLabel = formatEtaShort(vendorIngestQueue(activeVendor)?.eta_seconds);

                                let label = 'Start Scraping';
                                if (isPending) {
                                    label = aheadCount > 0
                                        ? `Pending — ${aheadCount} ahead${etaLabel ? ` (${etaLabel})` : ''}`
                                        : `Pending — next in line${etaLabel ? ` (${etaLabel})` : ''}`;
                                } else if (trackingScrape && activeVendor) {
                                    label = `Scraping ${activeLabel}… ${activeVendor.scraped || 0}/${activeVendor.total || 0} (${activeVendor.pct || 0}%)`;
                                } else if (isClaimed) {
                                    label = 'Scraping…';
                                } else if (trackingServerScrape) {
                                    label = 'Fetching prices (website)…';
                                }

                                let titleText;
                                if (isPending) {
                                    titleText = 'Another store is running first. Yours will start on its own when the line is clear.';
                                } else if (desktopRunnerBusy) {
                                    titleText = 'Price check in progress — use Stop Scraping to cancel.';
                                } else if (trackingServerScrape) {
                                    titleText = 'We are fetching vendor prices on our servers. You can leave this page. Use Stop Scraping to cancel.';
                                } else if (vendorList.length > 0) {
                                    const labels = vendorList
                                        .map((v) => v.label || (v.code || '').toUpperCase())
                                        .join(' / ');
                                    titleText = `Refresh vendor price and stock for all active listings (${labels} uses your computer for part of this).`;
                                } else {
                                    titleText = 'Refresh vendor price and stock for all active listings.';
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
                                                title="Stop the price check. Anything running on your computer or our servers should wind down in a few seconds."
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
                        {productsFetching && products.length === 0 ? (
                            <div className="flex justify-center py-16">
                                <div className="flex flex-col items-center gap-3">
                                    <div className="h-8 w-8 animate-spin rounded-full border-2 border-slate-200 dark:border-slate-700 border-t-accent-500" />
                                    <p className="text-sm text-slate-500 dark:text-slate-400">Loading products…</p>
                                </div>
                            </div>
                        ) : !productsFetching && filteredProducts.length === 0 ? (
                            <EmptyState
                                icon={Package}
                                title="No products"
                                description="Upload a catalog, use Ready to Upload in upload history, then Start Scraping for vendor data."
                                action={
                                    <Button variant="secondary" size="sm" onClick={handleBackToHistory}>
                                        View upload history
                                    </Button>
                                }
                            />
                        ) : (
                            <div className="relative">
                                {productsFetching && products.length > 0 ? (
                                    <div
                                        className="absolute inset-0 z-10 flex items-start justify-center bg-white/60 dark:bg-slate-900/60 pt-10 pointer-events-none"
                                        aria-hidden
                                    >
                                        <span className="inline-flex items-center gap-2 rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-600 dark:text-slate-300 shadow-sm">
                                            <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 dark:border-slate-600 border-t-accent-500" />
                                            Loading page…
                                        </span>
                                    </div>
                                ) : null}
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
                                        {showFixedColumns && (
                                            <>
                                                <th className="w-[70px] whitespace-nowrap text-right" title="Required when the store uses a fixed pricing tier.">Pack QTY</th>
                                                <th className="w-[70px] whitespace-nowrap text-right" title="Required when the store uses a fixed pricing tier.">Prep Fees</th>
                                                <th className="w-[70px] whitespace-nowrap text-right" title="Required when the store uses a fixed pricing tier.">Shipping</th>
                                            </>
                                        )}
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
                                                {showFixedColumns && (
                                                    <>
                                                        <td className="text-right align-middle whitespace-nowrap">
                                                            <EditableNumericCell
                                                                value={product.pack_qty}
                                                                fieldLabel="pack qty"
                                                                onSave={(v) => handleUpdateProductField(product, 'pack_qty', v)}
                                                            />
                                                        </td>
                                                        <td className="text-right align-middle whitespace-nowrap">
                                                            <EditableNumericCell
                                                                value={product.prep_fees}
                                                                fieldLabel="prep fees"
                                                                onSave={(v) => handleUpdateProductField(product, 'prep_fees', v)}
                                                            />
                                                        </td>
                                                        <td className="text-right align-middle whitespace-nowrap">
                                                            <EditableNumericCell
                                                                value={product.shipping_fees}
                                                                fieldLabel="shipping fees"
                                                                onSave={(v) => handleUpdateProductField(product, 'shipping_fees', v)}
                                                            />
                                                        </td>
                                                    </>
                                                )}
                                                <td className="text-center align-middle whitespace-nowrap">
                                                    <Badge
                                                        variant={syncStatusVariant[status] || syncStatusVariant.pending}
                                                        title={
                                                            product.scrape_error && (status === 'failed' || status === 'needs_attention')
                                                                ? `Last scrape error: ${product.scrape_error}`
                                                                : undefined
                                                        }
                                                    >
                                                        {syncStatusLabel[status] || syncStatusLabel.pending}
                                                    </Badge>
                                                </td>
                                                <td className="text-right text-sm text-slate-600 dark:text-slate-400 align-middle whitespace-nowrap">{margin}</td>
                                                <td className="text-slate-500 dark:text-slate-400 text-xs align-middle whitespace-nowrap">{formatLastStatus(product)}</td>
                                                <td className="text-right align-middle">
                                                    {(status === 'needs_attention' || status === 'failed') ? (
                                                        <button
                                                            onClick={() => handleResetSyncStatus(product)}
                                                            disabled={resettingId === product.id}
                                                            title={
                                                                product.scrape_error
                                                                    ? `Reason: ${product.scrape_error}. Click to clear the failure flag, then run Scrape data to retry.`
                                                                    : 'Clear the failure flag, then run Scrape data to retry.'
                                                            }
                                                            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-accent-600 dark:text-accent-400 hover:bg-accent-50 dark:hover:bg-accent-900/20 transition"
                                                        >
                                                            <RotateCcw className={`h-3.5 w-3.5 ${resettingId === product.id ? 'animate-spin' : ''}`} />
                                                            {status === 'failed' ? 'Retry' : 'Resync'}
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
                            </div>
                        )}
                    </div>

                    {totalProductCount > PRODUCTS_PER_PAGE && (
                        <div className="flex items-center justify-between border-t border-slate-200 dark:border-slate-700 px-4 py-3">
                            <p className="text-sm text-slate-500 dark:text-slate-400">
                                Showing <span className="font-medium text-slate-700 dark:text-slate-300">{totalProductCount === 0 ? 0 : (safePage - 1) * PRODUCTS_PER_PAGE + 1}</span>–<span className="font-medium text-slate-700 dark:text-slate-300">{Math.min(safePage * PRODUCTS_PER_PAGE, totalProductCount)}</span> of{' '}
                                <span className="font-medium text-slate-700 dark:text-slate-300">{totalProductCount.toLocaleString()}</span> products
                            </p>
                            <div className="flex items-center gap-1">
                                <button
                                    type="button"
                                    onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                                    disabled={safePage <= 1 || productsFetching}
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
                                                disabled={productsFetching}
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
                                    onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                                    disabled={safePage >= totalPages || productsFetching}
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

            <MydealUploadModal
                open={mydealUploadOpen && !!selectedStore}
                storeId={selectedStore}
                onClose={() => setMydealUploadOpen(false)}
                onComplete={() => {
                    getCatalogStores(selectedMarketplace || null).then((r) => setStoreList(Array.isArray(r.data) ? r.data : []));
                }}
            />

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
            <ConfirmModal
                open={resetPendingModalOpen}
                title="Reset all to Pending"
                message="Every active product listing for this store will be marked Pending (scraped/synced rows included). This clears scrape errors so you can run Start Scraping again. It does not fetch prices by itself."
                confirmLabel="Reset to Pending"
                cancelLabel="Cancel"
                variant="primary"
                loading={resetPendingLoading}
                onConfirm={handleResetAllPendingConfirm}
                onCancel={() => !resetPendingLoading && setResetPendingModalOpen(false)}
            />
        </div>
    );
}
