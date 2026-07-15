import { useCallback, useEffect, useState } from 'react';
import { Pencil, RefreshCw, Send, Trash2 } from 'lucide-react';
import Button from '../ui/Button';
import { deleteListing, getListings, publishListings } from '../../services/listingService';
import ListingFormModal from './ListingFormModal';

const STATUS_STYLES = {
    uploaded_staging: 'bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300',
    uploaded_production: 'bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300',
};

const STATUS_LABELS = {
    uploaded_staging: 'On marketplace (staging)',
    uploaded_production: 'On marketplace',
};

const ACTION_LABELS = {
    create: 'Create',
    mapped: 'Mapped',
};

/** Live marketplace listings the user can edit inventory/price and re-publish. */
export default function InventoryManagementPanel({ storeId, marketplaceCode = '', reloadNonce = 0, onMessage }) {
    const [listings, setListings] = useState([]);
    const [loading, setLoading] = useState(false);
    const [publishing, setPublishing] = useState(false);
    const [editListing, setEditListing] = useState(null);
    const [editOpen, setEditOpen] = useState(false);
    const [search, setSearch] = useState('');

    const load = useCallback(() => {
        if (!storeId) return;
        setLoading(true);
        getListings(storeId, { view: 'inventory', search: search || undefined })
            .then((res) => setListings(Array.isArray(res.data) ? res.data : []))
            .catch(() => onMessage?.('Failed to load inventory.', 'error'))
            .finally(() => setLoading(false));
    }, [storeId, search, onMessage]);

    useEffect(() => {
        load();
    }, [load, reloadNonce]);

    const handlePublish = (ids = null) => {
        setPublishing(true);
        publishListings(storeId, ids)
            .then((res) => {
                onMessage?.(res.data?.message || 'Inventory updated on marketplace.', 'success');
                load();
            })
            .catch((err) => {
                onMessage?.(err.response?.data?.detail || err.response?.data?.message || 'Publish failed.', 'error');
                load();
            })
            .finally(() => setPublishing(false));
    };

    const handleDelete = (listing) => {
        if (!window.confirm(`Remove "${listing.external_variant_key}" from the marketplace?`)) return;
        deleteListing(storeId, listing.id)
            .then(() => {
                onMessage?.(`Removed "${listing.external_variant_key}".`, 'success');
                load();
            })
            .catch((err) => onMessage?.(err.response?.data?.detail || 'Delete failed.', 'error'));
    };

    return (
        <div className="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 dark:border-slate-700 px-4 py-3">
                <div>
                    <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Inventory management</h2>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                        Successfully created or mapped listings on the marketplace. Edit stock or price, then publish.
                    </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <input
                        type="search"
                        placeholder="Search SKU or title…"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-1.5 text-sm text-slate-900 dark:text-slate-100"
                    />
                    <Button variant="secondary" size="sm" onClick={load} disabled={loading}>
                        <RefreshCw className={`mr-1.5 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
                        Refresh
                    </Button>
                    <Button
                        variant="primary"
                        size="sm"
                        onClick={() => handlePublish()}
                        disabled={publishing || listings.length === 0}
                    >
                        <Send className="mr-1.5 h-4 w-4" />
                        {publishing ? 'Publishing…' : `Publish all (${listings.length})`}
                    </Button>
                </div>
            </div>

            <div className="overflow-x-auto">
                {loading && listings.length === 0 ? (
                    <p className="px-4 py-6 text-sm text-slate-500 dark:text-slate-400">Loading inventory…</p>
                ) : listings.length === 0 ? (
                    <p className="px-4 py-6 text-sm text-slate-500 dark:text-slate-400">
                        No listings on the marketplace yet. Create or map listings, publish them from Created products,
                        then manage inventory here.
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
                                <th className="px-4 py-2.5">Last push</th>
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
                                        <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[l.status] || ''}`}>
                                            {STATUS_LABELS[l.status] || l.status}
                                        </span>
                                    </td>
                                    <td className="px-4 py-2.5 text-xs text-slate-500 dark:text-slate-400">
                                        {l.last_uploaded_at ? new Date(l.last_uploaded_at).toLocaleString() : '—'}
                                    </td>
                                    <td className="px-4 py-2.5">
                                        <div className="flex items-center justify-end gap-1">
                                            <button
                                                type="button"
                                                title="Publish changes"
                                                className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
                                                onClick={() => handlePublish([l.id])}
                                                disabled={publishing}
                                            >
                                                <Send className="h-4 w-4" />
                                            </button>
                                            <button
                                                type="button"
                                                title="Edit inventory"
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
