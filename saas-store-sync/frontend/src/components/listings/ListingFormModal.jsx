import { useEffect, useState } from 'react';
import { X } from 'lucide-react';
import Button from '../ui/Button';
import Input from '../ui/Input';
import { createListing, updateListing } from '../../services/listingService';

const EMPTY_FORM = {
    product_key: '',
    variant_key: '',
    title: '',
    description: '',
    brand: '',
    category: '',
    sku: '',
    barcode: '',
    image_urls: '',
    inventory: '0',
    infinite_quantity: false,
    original_price: '',
    sale_price: '',
};

function Textarea({ label, rows = 3, ...props }) {
    return (
        <div>
            {label && (
                <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">{label}</label>
            )}
            <textarea
                rows={rows}
                className="block w-full rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 shadow-sm focus:border-accent-500 focus:ring-1 focus:ring-accent-500 px-3 py-2 text-sm outline-none"
                {...props}
            />
        </div>
    );
}

/**
 * Create or edit a single managed-store listing ("created product").
 * Pass `listing` to edit; omit it to create.
 */
export default function ListingFormModal({ open, onClose, onSaved, storeId, listing = null }) {
    const isEdit = !!listing;
    const [form, setForm] = useState(EMPTY_FORM);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        if (!open) return;
        setError('');
        if (listing) {
            setForm({
                product_key: listing.external_product_key || '',
                variant_key: listing.external_variant_key || '',
                title: listing.title || '',
                description: listing.description || '',
                brand: listing.brand || '',
                category: listing.category || '',
                sku: listing.sku || '',
                barcode: listing.barcode || '',
                image_urls: listing.image_urls || '',
                inventory: String(listing.inventory ?? 0),
                infinite_quantity: !!listing.infinite_quantity,
                original_price: String(listing.original_price ?? ''),
                sale_price: String(listing.sale_price ?? ''),
            });
        } else {
            setForm(EMPTY_FORM);
        }
    }, [open, listing]);

    if (!open) return null;

    const set = (field) => (e) => {
        const value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
        setForm((f) => ({ ...f, [field]: value }));
    };

    const handleSubmit = (e) => {
        e.preventDefault();
        setSaving(true);
        setError('');
        const payload = {
            ...form,
            inventory: parseInt(form.inventory, 10) || 0,
            original_price: form.original_price === '' ? 0 : form.original_price,
            sale_price: form.sale_price === '' ? 0 : form.sale_price,
        };
        const req = isEdit
            ? updateListing(storeId, listing.id, payload)
            : createListing(storeId, payload);
        req
            .then((res) => {
                onSaved?.(res.data);
                onClose();
            })
            .catch((err) => {
                const d = err.response?.data;
                let msg = d?.detail;
                if (!msg && d && typeof d === 'object') {
                    msg = Object.entries(d)
                        .flatMap(([k, v]) => (Array.isArray(v) ? v : [v]).map((x) => `${k}: ${x}`))
                        .join('. ');
                }
                setError(msg || 'Failed to save listing');
            })
            .finally(() => setSaving(false));
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="fixed inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />
            <div
                className="relative flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 shadow-xl"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex shrink-0 items-center justify-between border-b border-slate-200 dark:border-slate-700 px-6 py-4">
                    <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
                        {isEdit ? `Edit listing — ${listing.external_variant_key}` : 'Create listing'}
                    </h2>
                    <button type="button" className="rounded-md p-2 text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800" onClick={onClose}>
                        <X className="h-5 w-5" />
                    </button>
                </div>

                <form onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col">
                    <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
                        {error && (
                            <div className="mb-4 rounded-lg border border-rose-200 dark:border-rose-800 bg-rose-50 dark:bg-rose-900/20 px-4 py-3 text-sm text-rose-700 dark:text-rose-300">
                                {error}
                            </div>
                        )}
                        {isEdit && Array.isArray(listing?.validation_errors_json) && listing.validation_errors_json.length > 0 && (
                            <div className="mb-4 rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 px-4 py-3 text-xs text-amber-700 dark:text-amber-300">
                                {listing.validation_errors_json.join(' ')}
                            </div>
                        )}
                        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                            <Input label="SKU" value={form.sku} onChange={set('sku')} required />
                            <Input label="Brand" value={form.brand} onChange={set('brand')} required />
                            <div className="sm:col-span-2">
                                <Input label="Title" value={form.title} onChange={set('title')} required />
                            </div>
                            <div className="sm:col-span-2">
                                <Textarea label="Description" rows={4} value={form.description} onChange={set('description')} required />
                            </div>
                            <Input label="Category" placeholder="e.g. Apparel > T-Shirts" value={form.category} onChange={set('category')} />
                            <Input label="Barcode (optional)" value={form.barcode} onChange={set('barcode')} />
                            <div className="sm:col-span-2">
                                <Textarea
                                    label="Image URLs (separate with | , ; or new lines)"
                                    rows={2}
                                    value={form.image_urls}
                                    onChange={set('image_urls')}
                                    required
                                />
                            </div>
                            <Input
                                label="Product Key (defaults to SKU)"
                                placeholder="Groups variants of the same product"
                                value={form.product_key}
                                onChange={set('product_key')}
                            />
                            <Input
                                label="Variant Key (defaults to SKU)"
                                placeholder="Unique per variant"
                                value={form.variant_key}
                                onChange={set('variant_key')}
                            />
                            <Input
                                label="Original Price"
                                type="number"
                                min="0"
                                step="0.01"
                                value={form.original_price}
                                onChange={set('original_price')}
                                required
                            />
                            <Input
                                label="Sale Price (≤ original)"
                                type="number"
                                min="0"
                                step="0.01"
                                value={form.sale_price}
                                onChange={set('sale_price')}
                                required
                            />
                            <Input
                                label="Inventory"
                                type="number"
                                min="0"
                                step="1"
                                value={form.inventory}
                                onChange={set('inventory')}
                                disabled={form.infinite_quantity}
                            />
                            <label className="mt-6 flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
                                <input
                                    type="checkbox"
                                    className="h-4 w-4 rounded border-slate-300"
                                    checked={form.infinite_quantity}
                                    onChange={set('infinite_quantity')}
                                />
                                Infinite quantity (never out of stock)
                            </label>
                        </div>
                    </div>
                    <div className="flex shrink-0 items-center justify-end gap-2 border-t border-slate-200 dark:border-slate-700 px-6 py-4">
                        <Button variant="secondary" type="button" onClick={onClose} disabled={saving}>
                            Cancel
                        </Button>
                        <Button variant="primary" type="submit" disabled={saving}>
                            {saving ? 'Saving…' : isEdit ? 'Save changes' : 'Create listing'}
                        </Button>
                    </div>
                </form>
            </div>
        </div>
    );
}
