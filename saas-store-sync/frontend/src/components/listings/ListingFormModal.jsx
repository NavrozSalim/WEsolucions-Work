import { useEffect, useMemo, useState } from 'react';
import { X } from 'lucide-react';
import Button from '../ui/Button';
import Input from '../ui/Input';
import Select from '../ui/Select';
import { createListing, updateListing } from '../../services/listingService';
import { getStore } from '../../services/storeService';
import ListingPhotoUploader from './ListingPhotoUploader';
import { ReverbCategorySelect, ReverbConditionSelect } from './ReverbCatalogSelects';

const EMPTY_LASOO = {
    action: 'create',
    vendor_name: '',
    vendor_url: '',
    vendor_id: '',
    marketplace_name: '',
    store_name: '',
    product_key: '',
    variant_key: '',
    title: '',
    description: '',
    brand: '',
    category: '',
    sku: '',
    barcode: '',
    option_1_name: '',
    option_1_value: '',
    option_2_name: '',
    option_2_value: '',
    option_3_name: '',
    option_3_value: '',
    option_4_name: '',
    option_4_value: '',
    variation_image_url: '',
    source_vendor_code: '',
    image_urls: '',
    inventory: '0',
    infinite_quantity: false,
    original_price: '',
    sale_price: '',
};

const EMPTY_REVERB = {
    action: 'create',
    vendor_name: '',
    vendor_url: '',
    vendor_id: '',
    marketplace_name: '',
    store_name: '',
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
    upc_does_not_apply: false,
    source_vendor_code: '',
    image_urls: '',
    publish_status: 'draft',
    free_shipping: true,
};

function isNoraCode(code) {
    return /nora/i.test(String(code || ''));
}

function vendorUrlPlaceholder(code) {
    const c = String(code || '').toLowerCase();
    if (c.includes('amazonau') || c.includes('amazon_au') || c.includes('amazon-au')) {
        return 'https://www.amazon.com.au/dp/…';
    }
    if (c.includes('amazon')) return 'https://www.amazon.com/dp/…';
    if (c.includes('ebayau') || c.includes('ebay_au') || c.includes('ebay-au')) {
        return 'https://www.ebay.com.au/itm/…';
    }
    if (c.includes('ebay')) return 'https://www.ebay.com/itm/…';
    if (c.includes('costco')) return 'https://www.costco.com.au/…';
    if (c.includes('vevor')) return 'https://www.vevor.com.au/…';
    if (c.includes('heb')) return 'https://www.heb.com/…';
    return 'https://… (product page used for price & stock scrape)';
}

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
 * Field order matches the bulk listing CSV template for Reverb / Lasoo.
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
    const [storeVendors, setStoreVendors] = useState([]);
    const [storeMeta, setStoreMeta] = useState({ name: '', marketplace_name: '' });

    useEffect(() => {
        if (!open || !storeId) return;
        let cancelled = false;
        getStore(storeId)
            .then((res) => {
                if (cancelled) return;
                const data = res.data || {};
                setStoreMeta({
                    name: data.name || '',
                    marketplace_name: data.marketplace_name || '',
                });
                const settings = data.vendor_price_settings || [];
                const opts = settings
                    .filter((vp) => vp.vendor_code || vp.vendor)
                    .map((vp) => ({
                        code: String(vp.vendor_code || '').trim().toLowerCase(),
                        name: vp.vendor_name || vp.vendor_code || 'Vendor',
                    }))
                    .filter((v) => v.code);
                setStoreVendors(opts);
            })
            .catch(() => {
                if (!cancelled) {
                    setStoreVendors([]);
                    setStoreMeta({ name: '', marketplace_name: '' });
                }
            });
        return () => {
            cancelled = true;
        };
    }, [open, storeId]);

    useEffect(() => {
        if (!open) return;
        setError('');
        const defaults = {
            marketplace_name: storeMeta.marketplace_name || '',
            store_name: storeMeta.name || '',
        };
        if (listing) {
            if (isReverb) {
                setForm({
                    ...EMPTY_REVERB,
                    ...defaults,
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
                    upc_does_not_apply: listing.barcode
                        ? false
                        : !!listing.upc_does_not_apply,
                    source_vendor_code: listing.source_vendor_code || '',
                    vendor_name: listing.vendor_name || '',
                    vendor_url: listing.vendor_url || '',
                    vendor_id: listing.vendor_id || '',
                    image_urls: listing.image_urls || '',
                    publish_status: listing.publish_status === 'live' ? 'live' : 'draft',
                    free_shipping: listing.free_shipping !== false,
                    marketplace_name: listing.marketplace_name || defaults.marketplace_name,
                    store_name: listing.store_name || defaults.store_name,
                });
            } else {
                setForm({
                    ...EMPTY_LASOO,
                    ...defaults,
                    action: listing.action || 'create',
                    product_key: listing.external_product_key || '',
                    variant_key: listing.external_variant_key || '',
                    title: listing.title || '',
                    description: listing.description || '',
                    brand: listing.brand || '',
                    category: listing.category || '',
                    sku: listing.sku || '',
                    barcode: listing.barcode || '',
                    option_1_name: listing.option_1_name || '',
                    option_1_value: listing.option_1_value || '',
                    option_2_name: listing.option_2_name || '',
                    option_2_value: listing.option_2_value || '',
                    option_3_name: listing.option_3_name || '',
                    option_3_value: listing.option_3_value || '',
                    option_4_name: listing.option_4_name || '',
                    option_4_value: listing.option_4_value || '',
                    variation_image_url: listing.variation_image_url || '',
                    source_vendor_code: listing.source_vendor_code || '',
                    vendor_name: listing.vendor_name || '',
                    vendor_url: listing.vendor_url || '',
                    vendor_id: listing.vendor_id || '',
                    image_urls: listing.image_urls || '',
                    inventory: String(listing.inventory ?? 0),
                    infinite_quantity: !!listing.infinite_quantity,
                    original_price: String(listing.original_price ?? ''),
                    sale_price: String(listing.sale_price ?? ''),
                    marketplace_name: listing.marketplace_name || defaults.marketplace_name,
                    store_name: listing.store_name || defaults.store_name,
                });
            }
        } else {
            setForm({
                ...(isReverb ? EMPTY_REVERB : EMPTY_LASOO),
                ...defaults,
            });
        }
    }, [open, listing, isReverb, storeMeta.name, storeMeta.marketplace_name]);

    const vendorOptions = useMemo(() => {
        const opts = [{ value: '', label: storeVendors.length ? 'Select vendor…' : 'No vendors on store Price settings' }];
        storeVendors.forEach((v) => {
            opts.push({ value: v.code, label: v.name });
        });
        return opts;
    }, [storeVendors]);

    const selectedVendor = form.source_vendor_code || '';
    const noraSelected = isNoraCode(selectedVendor);
    const selectedVendorName =
        storeVendors.find((v) => v.code === selectedVendor)?.name || form.vendor_name || '';

    if (!open) return null;

    const set = (field) => (e) => {
        const value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
        setForm((f) => ({ ...f, [field]: value }));
    };

    const setVendor = (e) => {
        const code = e.target.value;
        const name = storeVendors.find((v) => v.code === code)?.name || '';
        setForm((f) => ({
            ...f,
            source_vendor_code: code,
            vendor_name: name,
            ...(isNoraCode(code)
                ? { vendor_url: f.vendor_url }
                : { vendor_id: isNoraCode(f.source_vendor_code) ? '' : f.vendor_id }),
        }));
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
            setError('Add at least one photo (upload or paste Photo URLs).');
            setSaving(false);
            return;
        }
        if (storeVendors.length > 0 && !String(form.source_vendor_code || '').trim()) {
            setError('Select a Vendor Name from this store’s Price settings.');
            setSaving(false);
            return;
        }
        if (noraSelected && !String(form.vendor_id || '').trim()) {
            setError('Vendor ID is required for Nora Inventory.');
            setSaving(false);
            return;
        }
        if (selectedVendor && !noraSelected && !String(form.vendor_url || '').trim()) {
            setError('Vendor URL is required for the selected vendor.');
            setSaving(false);
            return;
        }
        let payload;
        if (isReverb) {
            const price = form.sale_price === '' ? 0 : form.sale_price;
            payload = {
                action: form.action,
                vendor_name: selectedVendorName || form.vendor_name || '',
                vendor_url: form.vendor_url,
                vendor_id: form.vendor_id || '',
                marketplace_name: form.marketplace_name || '',
                store_name: form.store_name || '',
                sku: form.sku,
                title: form.title,
                make: form.make,
                brand: form.make,
                model: form.model,
                description: form.description,
                finish: form.finish,
                year: form.year,
                condition_uuid: form.condition_uuid,
                condition: form.condition_uuid,
                category_uuid: form.category_uuid,
                category: form.category_uuid,
                sale_price: price,
                original_price: price,
                currency: form.currency || 'USD',
                inventory: parseInt(form.inventory, 10) || 0,
                barcode: form.barcode,
                upc_does_not_apply: String(form.barcode || '').trim()
                    ? false
                    : !!form.upc_does_not_apply,
                source_vendor_code: form.source_vendor_code || '',
                image_urls: form.image_urls,
                publish_status: form.publish_status === 'live' ? 'live' : 'draft',
                free_shipping: form.free_shipping !== false,
            };
        } else {
            payload = {
                ...form,
                vendor_name: selectedVendorName || form.vendor_name || '',
                source_vendor_code: form.source_vendor_code || '',
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
                        <p className="mb-4 text-xs text-slate-500 dark:text-slate-400">
                            Fields match the bulk listing template. Marketplace Name / Store Name can route to another of your stores (same as CSV).
                        </p>
                        {isEdit && Array.isArray(listing?.validation_errors_json) && listing.validation_errors_json.length > 0 && (
                            <div className="mb-4 rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 px-4 py-3 text-xs text-amber-700 dark:text-amber-300">
                                {listing.validation_errors_json.join(' ')}
                            </div>
                        )}

                        {isReverb ? (
                            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                                <div className="sm:col-span-2">
                                    <Select
                                        label="Vendor Name"
                                        value={form.source_vendor_code || ''}
                                        onChange={setVendor}
                                        options={vendorOptions}
                                        required={storeVendors.length > 0}
                                    />
                                    {storeVendors.length === 0 && (
                                        <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">
                                            Add vendors under Edit Store → Price, then create listings for scrape routing.
                                        </p>
                                    )}
                                </div>
                                {noraSelected ? (
                                    <div className="sm:col-span-2">
                                        <Input
                                            label="Vendor ID"
                                            placeholder="Nora BarCode after cleaning, e.g. 8FNZ100-DL-G1"
                                            value={form.vendor_id}
                                            onChange={set('vendor_id')}
                                            required
                                        />
                                    </div>
                                ) : (
                                    <div className="sm:col-span-2">
                                        <Input
                                            label="Vendor URL"
                                            placeholder={vendorUrlPlaceholder(selectedVendor)}
                                            value={form.vendor_url}
                                            onChange={set('vendor_url')}
                                            type="url"
                                            required={!!selectedVendor}
                                        />
                                    </div>
                                )}
                                <Input
                                    label="Marketplace Name"
                                    value={form.marketplace_name}
                                    onChange={set('marketplace_name')}
                                    placeholder="e.g. Reverb"
                                />
                                <Input
                                    label="Store Name"
                                    value={form.store_name}
                                    onChange={set('store_name')}
                                    placeholder="Defaults to this store"
                                />
                                {!isEdit && (
                                    <Select
                                        label="Action"
                                        value={form.action}
                                        onChange={set('action')}
                                        options={[
                                            { value: 'create', label: 'Create — new listing' },
                                            { value: 'mapped', label: 'Mapped — already on the store' },
                                        ]}
                                    />
                                )}
                                <Input label="SKU" value={form.sku} onChange={set('sku')} required />
                                <div className="sm:col-span-2">
                                    <Input label="Title" value={form.title} onChange={set('title')} required />
                                </div>
                                <Input label="Make" value={form.make} onChange={set('make')} required />
                                <Input label="Model" value={form.model} onChange={set('model')} required />
                                <div className="sm:col-span-2">
                                    <Textarea
                                        label="Description"
                                        rows={10}
                                        value={form.description}
                                        onChange={set('description')}
                                        required
                                        placeholder={'Write like a Reverb listing.\n\nUse a blank line between paragraphs.\n\nFeature Title----Detail on each line for a bullet list.\nOr start lines with - for bullets.'}
                                    />
                                    <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                                        Tip: blank line = new paragraph. Lines like{' '}
                                        <span className="font-medium">Feature----detail</span> or starting with{' '}
                                        <span className="font-medium">-</span> become a bullet list on Reverb.
                                    </p>
                                </div>
                                <Input label="Finish" value={form.finish} onChange={set('finish')} />
                                <Input label="Year" value={form.year} onChange={set('year')} />
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
                                <Input
                                    label="UPC"
                                    value={form.barcode}
                                    onChange={(e) => {
                                        const barcode = e.target.value;
                                        setForm((f) => ({
                                            ...f,
                                            barcode,
                                            upc_does_not_apply: barcode.trim()
                                                ? false
                                                : f.upc_does_not_apply,
                                        }));
                                    }}
                                />
                                <label className="mt-6 flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300 sm:col-span-2">
                                    <input
                                        type="checkbox"
                                        className="h-4 w-4 rounded border-slate-300"
                                        checked={!!form.upc_does_not_apply && !String(form.barcode || '').trim()}
                                        disabled={!!String(form.barcode || '').trim()}
                                        onChange={set('upc_does_not_apply')}
                                    />
                                    UPC Does Not Apply
                                </label>
                                <ListingPhotoUploader
                                    storeId={storeId}
                                    value={form.image_urls}
                                    onChange={(urls) => setForm((f) => ({ ...f, image_urls: urls }))}
                                    required
                                    label="Photo URLs"
                                />
                                <Select
                                    label="status"
                                    value={form.publish_status}
                                    onChange={set('publish_status')}
                                    options={[
                                        { value: 'draft', label: 'draft — save unpublished' },
                                        { value: 'live', label: 'live — publish immediately' },
                                    ]}
                                />
                                <label className="mt-6 flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
                                    <input
                                        type="checkbox"
                                        className="h-4 w-4 rounded border-slate-300"
                                        checked={form.free_shipping !== false}
                                        onChange={set('free_shipping')}
                                    />
                                    free_shipping
                                </label>
                            </div>
                        ) : (
                            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                                <div className="sm:col-span-2">
                                    <Select
                                        label="Vendor Name"
                                        value={form.source_vendor_code || ''}
                                        onChange={setVendor}
                                        options={vendorOptions}
                                        required={storeVendors.length > 0}
                                    />
                                    {storeVendors.length === 0 && (
                                        <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">
                                            Add vendors under Edit Store → Price, then create listings for scrape routing.
                                        </p>
                                    )}
                                </div>
                                {noraSelected ? (
                                    <div className="sm:col-span-2">
                                        <Input
                                            label="Vendor ID"
                                            placeholder="Nora BarCode after cleaning, e.g. 8FNZ100-DL-G1"
                                            value={form.vendor_id}
                                            onChange={set('vendor_id')}
                                            required
                                        />
                                    </div>
                                ) : (
                                    <div className="sm:col-span-2">
                                        <Input
                                            label="Vendor URL"
                                            placeholder={vendorUrlPlaceholder(selectedVendor)}
                                            value={form.vendor_url}
                                            onChange={set('vendor_url')}
                                            type="url"
                                            required={!!selectedVendor}
                                        />
                                    </div>
                                )}
                                <Input
                                    label="Marketplace Name"
                                    value={form.marketplace_name}
                                    onChange={set('marketplace_name')}
                                    placeholder="e.g. Lasoo"
                                />
                                <Input
                                    label="Store Name"
                                    value={form.store_name}
                                    onChange={set('store_name')}
                                    placeholder="Defaults to this store"
                                />
                                {!isEdit && (
                                    <Select
                                        label="Action"
                                        value={form.action}
                                        onChange={set('action')}
                                        options={[
                                            { value: 'create', label: 'Create — new listing' },
                                            { value: 'mapped', label: 'Mapped — already on the store' },
                                        ]}
                                    />
                                )}
                                <Input
                                    label="Product Key"
                                    placeholder="Shared parent key (defaults to SKU)"
                                    value={form.product_key}
                                    onChange={set('product_key')}
                                />
                                <Input
                                    label="Variant Key"
                                    placeholder="Unique per variant (defaults to SKU)"
                                    value={form.variant_key}
                                    onChange={set('variant_key')}
                                />
                                <Input label="SKU" value={form.sku} onChange={set('sku')} required />
                                <div className="sm:col-span-2 rounded-md border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/40 px-3 py-2 text-xs text-slate-600 dark:text-slate-300">
                                    Multi-variant: same <span className="font-medium">Product Key</span>, unique{' '}
                                    <span className="font-medium">Variant Key</span> / SKU, Option Name/Value pairs, and{' '}
                                    <span className="font-medium">Variation Img URL</span> per variant.
                                </div>
                                <Input
                                    label="Option 1 Name"
                                    placeholder="e.g. Size"
                                    value={form.option_1_name}
                                    onChange={set('option_1_name')}
                                />
                                <Input
                                    label="Option 1 Value"
                                    placeholder="e.g. XL"
                                    value={form.option_1_value}
                                    onChange={set('option_1_value')}
                                />
                                <Input
                                    label="Option 2 Name"
                                    placeholder="e.g. Color"
                                    value={form.option_2_name}
                                    onChange={set('option_2_name')}
                                />
                                <Input
                                    label="Option 2 Value"
                                    placeholder="e.g. Blue"
                                    value={form.option_2_value}
                                    onChange={set('option_2_value')}
                                />
                                <Input
                                    label="Option 3 Name"
                                    placeholder="Optional"
                                    value={form.option_3_name}
                                    onChange={set('option_3_name')}
                                />
                                <Input
                                    label="Option 3 Value"
                                    placeholder="Optional"
                                    value={form.option_3_value}
                                    onChange={set('option_3_value')}
                                />
                                <Input
                                    label="Option 4 Name"
                                    placeholder="Optional"
                                    value={form.option_4_name}
                                    onChange={set('option_4_name')}
                                />
                                <Input
                                    label="Option 4 Value"
                                    placeholder="Optional"
                                    value={form.option_4_value}
                                    onChange={set('option_4_value')}
                                />
                                <div className="sm:col-span-2">
                                    <Input
                                        label="Variation Img URL"
                                        placeholder="https://… (required for multi-variant rows)"
                                        value={form.variation_image_url}
                                        onChange={set('variation_image_url')}
                                    />
                                </div>
                                <div className="sm:col-span-2">
                                    <Input label="Title" value={form.title} onChange={set('title')} required />
                                </div>
                                <div className="sm:col-span-2">
                                    <Textarea label="Description" rows={4} value={form.description} onChange={set('description')} required />
                                </div>
                                <Input label="Brand" value={form.brand} onChange={set('brand')} required />
                                <Input label="Category" placeholder="e.g. Apparel > T-Shirts" value={form.category} onChange={set('category')} />
                                <Input label="Barcode" value={form.barcode} onChange={set('barcode')} />
                                <ListingPhotoUploader
                                    storeId={storeId}
                                    value={form.image_urls}
                                    onChange={(urls) => setForm((f) => ({ ...f, image_urls: urls }))}
                                    required
                                    label="Image URLs"
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
                                    Infinite Quantity
                                </label>
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
                                    label="Sale Price"
                                    type="number"
                                    min="0"
                                    step="0.01"
                                    value={form.sale_price}
                                    onChange={set('sale_price')}
                                    required
                                />
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
