import { useCallback, useEffect, useState } from 'react';
import { Pencil, RefreshCw, Send, Trash2 } from 'lucide-react';
import Button from '../ui/Button';
import { deleteListing, getListings, publishListings } from '../../services/listingService';
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

const FILTER_OPTIONS = [
    { id: 'all', label: 'All' },
    { id: 'ready', label: 'Create status' },
    { id: 'errors', label: 'Errors' },
];

/** Staging queue: new/mapped listings before or after publish attempt. */
export default function CreatedProductsPanel({ storeId, marketplaceCode = '', reloadNonce = 0, onMessage }) {
    const [listings, setListings] = useState([]);
    const [loading, setLoading] = useState(false);
    const [publishing, setPublishing] = useState(false);
    const [editListing, setEditListing] = useState(null);
    const [editOpen, setEditOpen] = useState(false);
    const [filter, setFilter] = useState('all');

    const load = useCallback(() => {
        if (!storeId) return;
        setLoading(true);
        const params = { view: 'created' };
        if (filter === 'ready') params.status = 'ready';
        if (filter === 'errors') params.errors = '1';
        getListings(storeId, params)
            .then((res) => setListings(Array.isArray(res.data) ? res.data : []))
            .catch(() => onMessage?.('Failed to load created products.', 'error'))
            .finally(() => setLoading(false));
    }, [storeId, filter, onMessage]);

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
            .catch((err) => onMessage?.(err.response?.data?.detail || 'Delete failed.', 'error'));
    };

    const publishableIds = listings
        .filter((l) => l.status === 'ready' || l.status === 'failed')
        .map((l) => l.id);
    const publishableCount = publishableIds.length;

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
                        onClick={() => handlePublish(publishableIds)}
                        disabled={publishing || publishableCount === 0}
                    >
                        <Send className="mr-1.5 h-4 w-4" />
                        {publishing ? 'Publishing…' : `Publish all (${publishableCount})`}
                    </Button>
                </div>
            </div>

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
                            {listings.map((l) => (
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
                                            title={Array.isArray(l.validation_errors_json) ? l.validation_errors_json.join(' ') : undefined}
                                        >
                                            {STATUS_LABELS[l.status] || l.status}
                                        </span>
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
