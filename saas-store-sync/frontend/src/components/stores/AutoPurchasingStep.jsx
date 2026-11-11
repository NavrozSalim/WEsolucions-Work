import { useEffect, useState } from 'react';
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

/** The vendor to save: blank when it is not offered for this Region (Region changed on step 1). */
export const sanitizeAutoBuyVendor = (region, vendor) => (
    autoBuyVendorsForRegion(region).some((v) => v.value === vendor) ? vendor : ''
);

export const NEW_ACCOUNT = 'new';
export const emptyAutoBuyAccount = () => ({ chrome_profile: '', email: '' });

/** Fields for the store create / update request from the Auto Purchasing step. */
export function buildAutoBuyPayload(form) {
    const vendor = sanitizeAutoBuyVendor(form.region, form.auto_buy_vendor);
    if (!vendor) return { auto_buy_vendor: '', auto_buy_account: null };
    if (form.auto_buy_account === NEW_ACCOUNT) {
        const profile = (form.auto_buy_account_new?.chrome_profile || '').trim();
        if (!profile) return { auto_buy_vendor: vendor };
        return {
            auto_buy_vendor: vendor,
            auto_buy_account_new: { chrome_profile: profile, email: (form.auto_buy_account_new?.email || '').trim() },
        };
    }
    return { auto_buy_vendor: vendor, auto_buy_account: form.auto_buy_account || null };
}

const REGION_LABELS = { USA: 'USA', AU: 'Australia' };

/**
 * Step 4 of the store wizard: the vendor this store's orders are bought from, and the purchase
 * account (Chrome profile on the worker laptop, signed in to that vendor) it buys with.
 */
export default function AutoPurchasingStep({ region, value, onChange, accountId, newAccount, onAccountChange }) {
    const options = autoBuyVendorsForRegion(region);
    const vendor = sanitizeAutoBuyVendor(region, value);
    const [accounts, setAccounts] = useState([]);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        let alive = true;
        if (!vendor) {
            setAccounts([]);
            return undefined;
        }
        setLoading(true);
        getMyPurchaseAccounts(region, vendor)
            .then((list) => {
                if (!alive) return;
                setAccounts(list);
                const known = list.some((a) => String(a.id) === String(accountId));
                if (!known && accountId !== NEW_ACCOUNT) {
                    onAccountChange(list.length ? String(list[0].id) : NEW_ACCOUNT, newAccount || emptyAutoBuyAccount());
                }
            })
            .catch(() => { if (alive) setAccounts([]); })
            .finally(() => { if (alive) setLoading(false); });
        return () => { alive = false; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [region, vendor]);

    const vendorName = autoBuyVendorLabel(vendor);
    const isNew = accountId === NEW_ACCOUNT;
    const setNew = (patch) => onAccountChange(NEW_ACCOUNT, { ...(newAccount || emptyAutoBuyAccount()), ...patch });

    return (
        <div className="space-y-4 w-full max-w-xl">
            <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50/80 dark:bg-slate-800/40 p-4 space-y-3">
                <Select
                    label="Auto buying vendor"
                    value={vendor}
                    onChange={(e) => onChange(e.target.value)}
                    options={[
                        { value: '', label: 'Off (not an auto-purchase store)' },
                        ...options,
                    ]}
                />
                <p className="text-sm text-slate-600 dark:text-slate-300">
                    Choices follow the Region on the Store step ({REGION_LABELS[region] || region || 'not set'}).
                    Choosing a vendor makes this an auto-purchase store: it appears on the Orders page, where its
                    marketplace orders are fetched, checked for profit and stock, and bought from this vendor only
                    when you click Process.
                </p>
            </div>

            {vendor && (
                <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50/80 dark:bg-slate-800/40 p-4 space-y-3">
                    <Select
                        label={`Purchase account (${vendorName})`}
                        value={isNew ? NEW_ACCOUNT : String(accountId || '')}
                        onChange={(e) => onAccountChange(e.target.value, newAccount || emptyAutoBuyAccount())}
                        options={[
                            ...accounts.map((a) => ({
                                value: String(a.id),
                                label: `${a.name} · Chrome profile "${a.chrome_profile || a.user_data_dir}"${a.email ? ` · ${a.email}` : ''}`,
                            })),
                            { value: NEW_ACCOUNT, label: '+ New purchase account' },
                        ]}
                        disabled={loading}
                    />
                    {isNew && (
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Input
                                label="Chrome profile on the laptop"
                                placeholder="e.g. Default or Profile 3"
                                value={newAccount?.chrome_profile || ''}
                                onChange={(e) => setNew({ chrome_profile: e.target.value })}
                            />
                            <Input
                                label="Account email (optional)"
                                placeholder="login email for this vendor"
                                value={newAccount?.email || ''}
                                onChange={(e) => setNew({ email: e.target.value })}
                            />
                        </div>
                    )}
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                        The worker laptop buys with this Chrome profile (folder name in C:\OrderBot\chrome-profiles).
                        It must be signed in to {vendorName} with the card saved on the vendor site. No passwords or
                        card numbers are stored here. One account can be used by several stores.
                    </p>
                    {isNew && !(newAccount?.chrome_profile || '').trim() && (
                        <p className="text-xs text-amber-700 dark:text-amber-400">
                            Enter the Chrome profile, or orders for this store cannot be bought with Process.
                        </p>
                    )}
                </div>
            )}
        </div>
    );
}
