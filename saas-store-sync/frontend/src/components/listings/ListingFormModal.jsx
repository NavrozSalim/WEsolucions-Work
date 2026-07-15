import { useEffect, useState } from 'react';
import { X } from 'lucide-react';
import Button from '../ui/Button';
import Input from '../ui/Input';
import Select from '../ui/Select';
import { createListing, updateListing } from '../../services/listingService';
import ListingPhotoUploader from './ListingPhotoUploader';
import { ReverbCategorySelect, ReverbConditionSelect } from './ReverbCatalogSelects';

const EMPTY_LASOO = {
    action: 'create',
    product_key: '',
    variant_key: '',
    title: '',
    description: '',
    brand: '',
    category: '',
    sku: '',
    barcode: '',
    vendor_url: '',
    image_urls: '',
    inventory: '0',
    infinite_quantity: false,
    original_price: '',
    sale_price: '',
};

const EMPTY_REVERB = {
    action: 'create',
    sku: '',
    title: '',
    make: '',
    model: '',
    description: '',
    finish: '',
    year: '',
    condition_uuid: '',
    category_uuid: '',
    sale_price: '',
    currency: 'USD',
    inventory: '1',
    barcode: '',
    upc_does_not_apply: true,
    vendor_url: '',
    image_urls: '',
    publish_status: 'draft',
    free_shipping: true,
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
 * When marketplaceCode is "reverb", shows Reverb fields (Make/Model/Condition/etc.).
 */
export default function ListingFormModal({
    open,
    onClose,
    onSaved,
    storeId,
    listing = null,
    marketplaceCode = '',
}) {
    const isEdit = !!listing;
    const isReverb = String(marketplaceCode || '').trim().toLowerCase() === 'reverb';
    const [form, setForm] = useState(isReverb ? EMPTY_REVERB : EMPTY_LASOO);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        if (!open) return;
        setError('');
        if (listing) {
            if (isReverb) {
                setForm({
                    action: listing.action || 'create',
                    sku: listing.sku || '',
                    title: listing.title || '',
                    make: listing.make || listing.brand || '',
                    model: listing.model || '',
                    description: listing.description || '',
                    finish: listing.finish || '',
                    year: listing.year || '',
                    condition_uuid: listing.condition_uuid || '',
                    category_uuid: listing.category_uuid || listing.category || '',
                    sale_price: String(listing.sale_price ?? listing.original_price ?? ''),
                    currency: listing.currency || 'USD',
                    inventory: String(listing.inventory ?? 1),
                    barcode: listing.barcode || '',
                    upc_does_not_apply: listing.upc_does_not_apply !== false,
                    vendor_url: listing.vendor_url || '',
                    image_urls: listing.image_urls || '',
                    publish_status: listing.publish_status === 'live' ? 'live' : 'draft',
                    free_shipping: listing.free_shipping !== false,
                });
            } else {
                setForm({
                    product_key: listing.external_product_key || '',
                    variant_key: listing.external_variant_key || '',
                    title: listing.title || '',
                    description: listing.description || '',
                    brand: listing.brand || '',
                    category: listing.category || '',
                    sku: listing.sku || '',
                    barcode: listing.barcode || '',
                    vendor_url: listing.vendor_url || '',
                    image_urls: listing.image_urls || '',
                    inventory: String(listing.inventory ?? 0),
                    infinite_quantity: !!listing.infinite_quantity,
                    original_price: String(listing.original_price ?? ''),
                    sale_price: String(listing.sale_price ?? ''),
                });
            }
        } else {
            setForm(isReverb ? EMPTY_REVERB : EMPTY_LASOO);
        }
    }, [open, listing, isReverb]);

    if (!open) return null;

    const set = (field) => (e) => {
        const value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
        setForm((f) => ({ ...f, [field]: value }));
    };

    const handleSubmit = (e) => {
        e.preventDefault();
        setSaving(true);
        setError('');
        const photoCount = String(form.image_urls || '')
            .split(/[|;\n,]+/)
            .map((s) => s.trim())
            .filter(Boolean).length;
        if (photoCount === 0) {
            setError('Upload at least one photo.');
            setSaving(false);
            return;
        }
        let payload;
        if (isReverb) {
            const price = form.sale_price === '' ? 0 : form.sale_price;
            payload = {
                action: form.action,
                sku: form.sku,
                title: form.title,
                make: form.make,
                brand: form.make,
                model: form.model,
                description: form.description,
                finish: form.finish,
                year: form.year,
                condition_uuid: form.condition_uuid,
                category_uuid: form.category_uuid,
                category: form.category_uuid,
                sale_price: price,
                original_price: price,
                currency: form.currency || 'USD',
                inventory: parseInt(form.inventory, 10) || 0,
                barcode: form.barcode,
                upc_does_not_apply: !!form.upc_does_not_apply,
                vendor_url: form.vendor_url,
                image_urls: form.image_urls,
                publish_status: form.publish_status === 'live' ? 'live' : 'draft',
                free_shipping: form.free_shipping !== false,
            };
        } else {
            payload = {
                ...form,
                inventory: parseInt(form.inventory, 10) || 0,
                original_price: form.original_price === '' ? 0 : form.original_price,
                sale_price: form.sale_price === '' ? 0 : form.sale_price,
            };
        }
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
                        {isEdit
                            ? `Edit listing — ${listing.sku || listing.external_variant_key}`
                            : isReverb
                              ? 'Create Reverb listing'
                              : 'Create listing'}
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
                        {!isEdit && (
                            <div className="mb-4">
                                <Select
                                    label="Action"
                                    value={form.action}
                                    onChange={set('action')}
                                    options={[
                                        { value: 'create', label: 'Create — new listing' },
                                        { value: 'mapped', label: 'Mapped — already on the store' },
                                    ]}
                                />
                            </div>
                        )}
                        {isEdit && Array.isArray(listing?.validation_errors_json) && listing.validation_errors_json.length > 0 && (
                            <div className="mb-4 rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 px-4 py-3 text-xs text-amber-700 dark:text-amber-300">
                                {listing.validation_errors_json.join(' ')}
                            </div>
                        )}

                        {isReverb ? (
                            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                                <Input label="SKU" value={form.sku} onChange={set('sku')} required />
                                <Input label="Make" value={form.make} onChange={set('make')} required />
                                <Input label="Model" value={form.model} onChange={set('model')} required />
                                <div className="sm:col-span-2">
                                    <Input label="Title" value={form.title} onChange={set('title')} required />
                                </div>
                                <div className="sm:col-span-2">
                                    <Textarea label="Description" rows={4} value={form.description} onChange={set('description')} required />
                                </div>
                                <Input label="Finish (optional)" value={form.finish} onChange={set('finish')} />
                                <Input label="Year (optional)" value={form.year} onChange={set('year')} />
                                <ReverbConditionSelect
                                    storeId={storeId}
                                    value={form.condition_uuid}
                                    onChange={(uuid) => setForm((f) => ({ ...f, condition_uuid: uuid }))}
                                    required
                                />
                                <ReverbCategorySelect
                                    storeId={storeId}
                                    value={form.category_uuid}
                                    onChange={(uuid) => setForm((f) => ({ ...f, category_uuid: uuid }))}
                                    required
                                />
                                <Input
                                    label="Price"
                                    type="number"
                                    min="0"
                                    step="0.01"
                                    value={form.sale_price}
                                    onChange={set('sale_price')}
                                    required
                                />
                                <Input label="Currency" value={form.currency} onChange={set('currency')} />
                                <Input
                                    label="Inventory"
                                    type="number"
                                    min="0"
                                    step="1"
                                    value={form.inventory}
                                    onChange={set('inventory')}
                                    required
                                />
                                <Input label="UPC (optional)" value={form.barcode} onChange={set('barcode')} />
                                <div className="sm:col-span-2">
                                    <Input
                                        label="Vendor URL"
                                        placeholder="https://www.amazon.com/dp/… (used to scrape price & stock)"
                                        value={form.vendor_url}
                                        onChange={set('vendor_url')}
                                        type="url"
                                        required
                                    />
                                </div>
                                <label className="mt-6 flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300 sm:col-span-2">
                                    <input
                                        type="checkbox"
                                        className="h-4 w-4 rounded border-slate-300"
                                        checked={!!form.upc_does_not_apply}
                                        onChange={set('upc_does_not_apply')}
                                    />
                                    UPC does not apply
                                </label>
                                <Select
                                    label="Status on Reverb"
                                    value={form.publish_status}
                                    onChange={set('publish_status')}
                                    options={[
                                        { value: 'draft', label: 'Draft — save unpublished' },
                                        { value: 'live', label: 'Live — publish immediately' },
                                    ]}
                                />
                                <label className="mt-6 flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
                                    <input
                                        type="checkbox"
                                        className="h-4 w-4 rounded border-slate-300"
                                        checked={form.free_shipping !== false}
                                        onChange={set('free_shipping')}
                                    />
                                    Free shipping
                                </label>
                                <div className="sm:col-span-2">
                                    <ListingPhotoUploader
                                        storeId={storeId}
                                        value={form.image_urls}
                                        onChange={(urls) => setForm((f) => ({ ...f, image_urls: urls }))}
                                        required
                                    />
                                </div>
                            </div>
                        ) : (
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
                                    <Input
                                        label="Vendor / source URL (optional)"
                                        placeholder="https://… product page you source this from"
                                        value={form.vendor_url}
                                        onChange={set('vendor_url')}
                                        type="url"
                                    />
                                </div>
                                <div className="sm:col-span-2">
                                    <ListingPhotoUploader
                                        storeId={storeId}
                                        value={form.image_urls}
                                        onChange={(urls) => setForm((f) => ({ ...f, image_urls: urls }))}
                                        required
                                    />
                                </div>                                <Input
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
                        )}
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
