import { useCallback, useEffect, useState } from 'react';
import { Pencil, RefreshCw, Send, Trash2 } from 'lucide-react';
import Button from '../ui/Button';
import { deleteListing, getCreatedListings, publishListings } from '../../services/listingService';
import ListingFormModal from './ListingFormModal';

const STATUS_STYLES = {
    ready: 'bg-sky-50 dark:bg-sky-900/30 text-sky-700 dark:text-sky-300',
    validation_failed: 'bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300',
    failed: 'bg-rose-50 dark:bg-rose-900/30 text-rose-700 dark:text-rose-300',
    draft: 'bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300',
};

const STATUS_LABELS = {
    ready: 'Create',
    validation_failed: 'Error',
    failed: 'Push failed',
    draft: 'Draft',
};

const ACTION_LABELS = {
    create: 'Create',
    mapped: 'Mapped',
};

function listingErrorText(listing) {
    const errs = Array.isArray(listing?.validation_errors_json)
        ? listing.validation_errors_json.filter((x) => typeof x === 'string' && x.trim())
        : [];
    if (errs.length) return errs.join(' ');
    const resp = listing?.marketplace_response_json;
    if (resp && typeof resp === 'object') {
        if (typeof resp.error === 'string' && resp.error.trim()) return resp.error.trim();
        if (typeof resp.message === 'string' && resp.message.trim()) return resp.message.trim();
    }
    return '';
}

const PAGE_SIZE = 10;

const FILTER_OPTIONS = [
    { id: 'all', label: 'All' },
    { id: 'ready', label: 'Create status' },
    { id: 'errors', label: 'Errors' },
];

/** Staging queue: new/mapped listings before or after publish attempt. */
export default function CreatedProductsPanel({ storeId, marketplaceCode = '', reloadNonce = 0, onMessage }) {
    const [listings, setListings] = useState([]);
    const [totalCount, setTotalCount] = useState(0);
    const [publishableCount, setPublishableCount] = useState(0);
    const [page, setPage] = useState(1);
    const [loading, setLoading] = useState(false);
    const [publishing, setPublishing] = useState(false);
    const [editListing, setEditListing] = useState(null);
    const [editOpen, setEditOpen] = useState(false);
    const [filter, setFilter] = useState('all');

    useEffect(() => {
        setPage(1);
    }, [filter, storeId]);

    const load = useCallback(() => {
        if (!storeId) return;
        setLoading(true);
        getCreatedListings(storeId, {
            page,
            pageSize: PAGE_SIZE,
            status: filter === 'ready' ? 'ready' : undefined,
            errors: filter === 'errors',
        })
            .then((res) => {
                setListings(Array.isArray(res.data) ? res.data : []);
                setTotalCount(Number.isFinite(res.count) ? res.count : 0);
                if (Number.isFinite(res.publishableCount)) {
                    setPublishableCount(res.publishableCount);
                } else {
                    setPublishableCount(
                        (Array.isArray(res.data) ? res.data : []).filter(
                            (l) => l.status === 'ready' || l.status === 'failed',
                        ).length,
                    );
                }
                if (res.page > res.totalPages && res.totalPages >= 1) {
                    setPage(res.totalPages);
                }
            })
            .catch((err) => {
                if (err.response?.status === 404 && page > 1) {
                    setPage(1);
                    return;
                }
                onMessage?.('Failed to load created products.', 'error');
            })
            .finally(() => setLoading(false));
    }, [storeId, page, filter, onMessage]);

    useEffect(() => {
        load();
    }, [load, reloadNonce]);

    const handlePublish = (ids = null) => {
        setPublishing(true);
        publishListings(storeId, ids)
            .then((res) => {
                onMessage?.(res.data?.message || 'Published to marketplace.', 'success');
                load();
            })
            .catch((err) => {
                onMessage?.(err.response?.data?.detail || err.response?.data?.message || 'Publish failed.', 'error');
                load();
            })
            .finally(() => setPublishing(false));
    };

    const handleDelete = (listing) => {
        if (!window.confirm(`Delete "${listing.external_variant_key}" from this app and the marketplace?`)) return;
        deleteListing(storeId, listing.id)
            .then(() => {
                onMessage?.(`Deleted "${listing.external_variant_key}".`, 'success');
                load();
            })
            .catch((err) => {
                const d = err.response?.data?.detail;
                const msg = typeof d === 'string' && d.trim()
                    ? d
                    : (Array.isArray(d) ? d.filter(Boolean).join(' ') : '') || 'Delete failed.';
                onMessage?.(msg, 'error');
            });
    };

    const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE) || 1);
    const safePage = Math.min(Math.max(1, page), totalPages);
    const rangeStart = totalCount === 0 ? 0 : (safePage - 1) * PAGE_SIZE + 1;
    const rangeEnd = Math.min(safePage * PAGE_SIZE, totalCount);

    return (
        <div className="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 dark:border-slate-700 px-4 py-3">
                <div>
                    <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Created products</h2>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                        Listings from Create or Bulk upload. Fix errors, then publish to move them to Inventory management.
                    </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <div className="inline-flex rounded-lg border border-slate-200 dark:border-slate-600 p-0.5">
                        {FILTER_OPTIONS.map((opt) => (
                            <button
                                key={opt.id}
                                type="button"
                                onClick={() => setFilter(opt.id)}
                                className={`rounded-md px-2.5 py-1 text-xs font-semibold transition ${
                                    filter === opt.id
                                        ? 'bg-accent-500 text-white'
                                        : 'text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800'
                                }`}
                            >
                                {opt.label}
                            </button>
                        ))}
                    </div>
                    <Button variant="secondary" size="sm" onClick={load} disabled={loading}>
                        <RefreshCw className={`mr-1.5 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
                        Refresh
                    </Button>
                    <Button
                        variant="primary"
                        size="sm"
                        onClick={() => handlePublish(null)}
                        disabled={publishing || publishableCount === 0}
                    >
                        <Send className="mr-1.5 h-4 w-4" />
                        {publishing ? 'Publishing…' : `Publish all (${publishableCount})`}
                    </Button>
                </div>
            </div>
            {publishing && (
                <div className="flex items-center gap-2 border-b border-sky-200 bg-sky-50 px-4 py-2.5 text-sm text-sky-800 dark:border-sky-800 dark:bg-sky-950/40 dark:text-sky-200">
                    <RefreshCw className="h-4 w-4 shrink-0 animate-spin" />
                    Creating products on MyDeal. This can take several minutes. Do not click Publish again.
                </div>
            )}

            <div className="overflow-x-auto">
                {loading && listings.length === 0 ? (
                    <p className="px-4 py-6 text-sm text-slate-500 dark:text-slate-400">Loading created products…</p>
                ) : listings.length === 0 ? (
                    <p className="px-4 py-6 text-sm text-slate-500 dark:text-slate-400">
                        No created products yet. Use “Create Listing” or “Bulk Listing” in the toolbar above.
                    </p>
                ) : (
                    <table className="w-full text-left text-sm">
                        <thead className="bg-slate-50 dark:bg-slate-800 text-xs uppercase text-slate-500 dark:text-slate-400">
                            <tr>
                                <th className="px-4 py-2.5">SKU / Variant</th>
                                <th className="px-4 py-2.5">Title</th>
                                <th className="px-4 py-2.5">Action</th>
                                <th className="px-4 py-2.5">Price</th>
                                <th className="px-4 py-2.5">Stock</th>
                                <th className="px-4 py-2.5">Status</th>
                                <th className="px-4 py-2.5 text-right">Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {listings.map((l) => {
                                const errorText = listingErrorText(l);
                                return (
                                <tr key={l.id} className="border-t border-slate-100 dark:border-slate-800">
                                    <td className="px-4 py-2.5">
                                        <p className="font-medium text-slate-900 dark:text-slate-100">{l.sku || l.external_variant_key}</p>
                                    </td>
                                    <td className="max-w-[240px] truncate px-4 py-2.5 text-slate-700 dark:text-slate-300" title={l.title}>
                                        {l.title || '—'}
                                    </td>
                                    <td className="px-4 py-2.5 text-slate-600 dark:text-slate-400">
                                        {ACTION_LABELS[l.action] || l.action}
                                    </td>
                                    <td className="px-4 py-2.5 text-slate-700 dark:text-slate-300">
                                        ${Number(l.sale_price).toFixed(2)}
                                    </td>
                                    <td className="px-4 py-2.5 text-slate-700 dark:text-slate-300">
                                        {l.infinite_quantity ? '∞' : l.inventory}
                                    </td>
                                    <td className="px-4 py-2.5">
                                        <span
                                            className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[l.status] || STATUS_STYLES.draft}`}
                                            title={errorText || undefined}
                                        >
                                            {STATUS_LABELS[l.status] || l.status}
                                        </span>
                                        {errorText && (l.status === 'failed' || l.status === 'validation_failed') && (
                                            <p className="mt-1 max-w-[220px] text-xs text-rose-600 dark:text-rose-400" title={errorText}>
                                                {errorText}
                                            </p>
                                        )}
                                    </td>
                                    <td className="px-4 py-2.5">
                                        <div className="flex items-center justify-end gap-1">
                                            {(l.status === 'ready' || l.status === 'failed') && (
                                                <button
                                                    type="button"
                                                    title="Publish"
                                                    className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
                                                    onClick={() => handlePublish([l.id])}
                                                    disabled={publishing}
                                                >
                                                    <Send className="h-4 w-4" />
                                                </button>
                                            )}
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
                                                title="Delete"
                                                className="rounded-md p-1.5 text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-900/20"
                                                onClick={() => handleDelete(l)}
                                            >
                                                <Trash2 className="h-4 w-4" />
                                            </button>
                                        </div>
                                    </td>
                                </tr>
                                );
                            })}
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
