import { useEffect, useMemo, useState } from 'react';
import { Plus, ShoppingCart, Trash2 } from 'lucide-react';
import Button from '../ui/Button';
import Input from '../ui/Input';
import Select from '../ui/Select';
import { getMyPurchaseAccounts } from '../../services/storeService';

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

/** Labels for a store's vendor list, e.g. "Amazon AU, Costco AU". */
export const autoBuyVendorsLabel = (codes) => (codes || []).map(autoBuyVendorLabel).filter(Boolean).join(', ');

export const NEW_ACCOUNT = 'new';
const emptyNewAccount = () => ({ chrome_profile: '', email: '' });

/** Form rows from a saved store (``store.auto_buy_vendors`` from the API). */
export function autoBuyRowsFromStore(store) {
    return (store?.auto_buy_vendors || []).map((r) => ({
        vendor: r.vendor,
        account: r.account ? String(r.account.id) : '',
        account_new: emptyNewAccount(),
    }));
}

/** ``auto_buy_vendors`` for the store create / update request. Rows not valid for the Region are dropped. */
export function buildAutoBuyPayload(form) {
    const allowed = autoBuyVendorsForRegion(form.region).map((v) => v.value);
    const rows = (form.auto_buy_vendors || []).filter((r) => r.vendor && allowed.includes(r.vendor));
    return {
        auto_buy_vendors: rows.map((r) => {
            if (r.account === NEW_ACCOUNT) {
                const profile = (r.account_new?.chrome_profile || '').trim();
                return profile
                    ? { vendor: r.vendor, account_new: { chrome_profile: profile, email: (r.account_new?.email || '').trim() } }
                    : { vendor: r.vendor, account: null };
            }
            return { vendor: r.vendor, account: r.account || null };
        }),
    };
}

const REGION_LABELS = { USA: 'USA', AU: 'Australia' };

function accountLabel(a) {
    const where = a.chrome_profile || a.user_data_dir;
    return `${a.name} · profile "${where}"${a.email ? ` · ${a.email}` : ''}`;
}

/**
 * Step 4 of the store wizard: the vendors this store buys its orders from, each with the purchase
 * account (Chrome profile on the worker laptop, signed in to that vendor) it buys with.
 */
export default function AutoPurchasingStep({ region, rows, onRowsChange }) {
    const vendorOptions = autoBuyVendorsForRegion(region);
    const allowed = vendorOptions.map((v) => v.value);
    const visibleRows = (rows || []).filter((r) => !r.vendor || allowed.includes(r.vendor));
    const [accounts, setAccounts] = useState([]);

    useEffect(() => {
        let alive = true;
        getMyPurchaseAccounts(region)
            .then((list) => { if (alive) setAccounts(list); })
            .catch(() => { if (alive) setAccounts([]); });
        return () => { alive = false; };
    }, [region]);

    const accountsFor = useMemo(() => (vendor) => accounts.filter((a) => a.vendor === vendor), [accounts]);
    const defaultAccount = (vendor) => {
        const list = accountsFor(vendor);
        return list.length ? String(list[0].id) : NEW_ACCOUNT;
    };

    // Fill in a sensible account once the account list has loaded.
    useEffect(() => {
        if (!accounts.length || !visibleRows.some((r) => r.vendor && !r.account)) return;
        onRowsChange(visibleRows.map((r) => (r.vendor && !r.account ? { ...r, account: defaultAccount(r.vendor) } : r)));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [accounts]);

    const update = (i, patch) => onRowsChange(visibleRows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
    const remove = (i) => onRowsChange(visibleRows.filter((_, j) => j !== i));
    const used = visibleRows.map((r) => r.vendor);
    const free = vendorOptions.filter((v) => !used.includes(v.value));
    const add = () => {
        const vendor = free[0]?.value;
        if (!vendor) return;
        onRowsChange([...visibleRows, { vendor, account: defaultAccount(vendor), account_new: emptyNewAccount() }]);
    };

    return (
        <div className="w-full max-w-3xl space-y-4">
            <div>
                <h3 className="flex items-center gap-2 text-base font-semibold text-slate-900 dark:text-slate-100">
                    <ShoppingCart className="h-4 w-4 text-accent-600 dark:text-accent-400" aria-hidden />
                    Auto Purchasing
                </h3>
                <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
                    Vendors this store buys its orders from ({REGION_LABELS[region] || region || 'Region not set'}).
                    With at least one vendor the store appears on the Orders page, where orders are fetched, checked
                    for stock and profit, and bought only when you click Process.
                </p>
            </div>

            {visibleRows.length === 0 ? (
                <div className="rounded-xl border border-dashed border-slate-300 px-6 py-8 text-center dark:border-slate-600">
                    <p className="text-sm font-medium text-slate-700 dark:text-slate-200">Not an auto-purchase store</p>
                    <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Add a vendor to buy this store&apos;s orders automatically.</p>
                    <Button type="button" variant="secondary" size="sm" className="mt-4" onClick={add} disabled={!free.length}>
                        <Plus className="mr-1.5 inline h-4 w-4" aria-hidden /> Add vendor
                    </Button>
                </div>
            ) : (
                <div className="space-y-3">
                    {visibleRows.map((row, i) => {
                        const list = accountsFor(row.vendor);
                        const isNew = row.account === NEW_ACCOUNT;
                        const missingProfile = isNew && !(row.account_new?.chrome_profile || '').trim();
                        return (
                            <div key={`${row.vendor}-${i}`} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-900/60">
                                <div className="grid gap-3 sm:grid-cols-[minmax(0,12rem)_minmax(0,1fr)_auto] sm:items-end">
                                    <Select
                                        label="Vendor"
                                        value={row.vendor}
                                        onChange={(e) => update(i, { vendor: e.target.value, account: defaultAccount(e.target.value), account_new: emptyNewAccount() })}
                                        options={vendorOptions.filter((v) => v.value === row.vendor || !used.includes(v.value))}
                                    />
                                    <Select
                                        label="Purchase account"
                                        value={row.account || ''}
                                        onChange={(e) => update(i, { account: e.target.value })}
                                        options={[
                                            ...list.map((a) => ({ value: String(a.id), label: accountLabel(a) })),
                                            { value: NEW_ACCOUNT, label: '+ New purchase account…' },
                                        ]}
                                    />
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="sm"
                                        className="justify-center text-rose-600 hover:text-rose-700 sm:mb-0.5"
                                        onClick={() => remove(i)}
                                        title={`Remove ${autoBuyVendorLabel(row.vendor)}`}
                                    >
                                        <Trash2 className="h-4 w-4" aria-hidden />
                                        <span className="sr-only">Remove</span>
                                    </Button>
                                </div>
                                {isNew && (
                                    <div className="mt-3 grid gap-3 rounded-lg bg-slate-50 p-3 sm:grid-cols-2 dark:bg-slate-800/50">
                                        <Input
                                            label="Chrome profile on the laptop"
                                            placeholder="e.g. Default or Profile 3"
                                            value={row.account_new?.chrome_profile || ''}
                                            onChange={(e) => update(i, { account_new: { ...row.account_new, chrome_profile: e.target.value } })}
                                        />
                                        <Input
                                            label="Account email (optional)"
                                            placeholder={`${autoBuyVendorLabel(row.vendor)} login email`}
                                            value={row.account_new?.email || ''}
                                            onChange={(e) => update(i, { account_new: { ...row.account_new, email: e.target.value } })}
                                        />
                                        {missingProfile && (
                                            <p className="text-xs text-amber-700 sm:col-span-2 dark:text-amber-400">
                                                Enter the Chrome profile, or {autoBuyVendorLabel(row.vendor)} orders cannot be bought with Process.
                                            </p>
                                        )}
                                    </div>
                                )}
                            </div>
                        );
                    })}
                    {free.length > 0 && (
                        <Button type="button" variant="secondary" size="sm" onClick={add}>
                            <Plus className="mr-1.5 inline h-4 w-4" aria-hidden /> Add vendor
                        </Button>
                    )}
                </div>
            )}

            <p className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-xs leading-relaxed text-slate-600 dark:border-slate-700 dark:bg-slate-800/40 dark:text-slate-400">
                A purchase account is a Chrome profile on the worker laptop (a folder in C:\OrderBot\chrome-profiles)
                signed in to that vendor with a card saved on the vendor site. No passwords or card numbers are stored here.
                One account can serve several stores. An order whose items come from different vendors is bought by hand.
            </p>
        </div>
    );
}
