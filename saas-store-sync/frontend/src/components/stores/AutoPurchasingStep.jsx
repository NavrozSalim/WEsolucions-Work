import Select from '../ui/Select';

/** Auto buying vendors per store Region. Codes match the backend (Store.AUTO_BUY_VENDORS_BY_REGION). */
export const AUTO_BUY_VENDORS_BY_REGION = {
    USA: [
        { value: 'amazon', label: 'Amazon US' },
        { value: 'ebay', label: 'eBay US' },
    ],
    AU: [
        { value: 'amazon_au', label: 'Amazon AU' },
        { value: 'ebay_au', label: 'eBay AU' },
        { value: 'costco_au', label: 'Costco AU' },
        { value: 'costway_au', label: 'Costway AU' },
    ],
};

export const autoBuyVendorsForRegion = (region) => AUTO_BUY_VENDORS_BY_REGION[region] || [];

export const autoBuyVendorLabel = (code) => {
    const all = Object.values(AUTO_BUY_VENDORS_BY_REGION).flat();
    return all.find((v) => v.value === code)?.label || code || '';
};

/** The vendor to save: blank when it is not offered for this Region (Region changed on step 1). */
export const sanitizeAutoBuyVendor = (region, vendor) => (
    autoBuyVendorsForRegion(region).some((v) => v.value === vendor) ? vendor : ''
);

const REGION_LABELS = { USA: 'USA', AU: 'Australia' };

/** Step 4 of the store wizard: choose the vendor this store's orders are bought from. */
export default function AutoPurchasingStep({ region, value, onChange }) {
    const options = autoBuyVendorsForRegion(region);
    const current = sanitizeAutoBuyVendor(region, value);
    return (
        <div className="space-y-4 w-full max-w-xl">
            <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50/80 dark:bg-slate-800/40 p-4 space-y-3">
                <Select
                    label="Auto buying vendor"
                    value={current}
                    onChange={(e) => onChange(e.target.value)}
                    options={[
                        { value: '', label: 'Off (not an auto-purchase store)' },
                        ...options,
                    ]}
                />
                <p className="text-sm text-slate-600 dark:text-slate-300">
                    Choices follow the Region on the Store step ({REGION_LABELS[region] || region || 'not set'}).
                    Choosing a vendor makes this an auto-purchase store: it appears on the Orders page,
                    where its marketplace orders are fetched, checked for profit and stock, and bought
                    from this vendor only when you click Process.
                </p>
            </div>
        </div>
    );
}
