import Input from '../ui/Input';

export const emptyShopifyFields = () => ({
    shopify_enabled: false,
    shopify_shop_domain: '',
    shopify_client_id: '',
    shopify_client_secret: '',
    shopify_location_id: '',
});

export function shopifyFieldsFromStore(store) {
    if (!store) return emptyShopifyFields();
    return {
        shopify_enabled: !!store.shopify_enabled,
        shopify_shop_domain: store.shopify_shop_domain || '',
        shopify_client_id: '',
        shopify_client_secret: '',
        shopify_location_id: store.shopify_location_id || '',
    };
}

export function buildShopifyPayload(form) {
    const enabled = !!form.shopify_enabled;
    const payload = { shopify_enabled: enabled };
    if (!enabled) return payload;
    payload.shopify_shop_domain = (form.shopify_shop_domain || '').trim();
    payload.shopify_location_id = (form.shopify_location_id || '').trim();
    if ((form.shopify_client_id || '').trim()) {
        payload.shopify_client_id = form.shopify_client_id.trim();
    }
    if ((form.shopify_client_secret || '').trim()) {
        payload.shopify_client_secret = form.shopify_client_secret.trim();
    }
    return payload;
}

export function validateShopifyFields(form, { requireSecrets = true } = {}) {
    if (!form.shopify_enabled) return [];
    const errs = [];
    const domain = (form.shopify_shop_domain || '').trim().toLowerCase();
    if (!domain || !domain.includes('.myshopify.com')) {
        errs.push('Shopify shop domain must be the .myshopify.com address (not the public website)');
    }
    if (requireSecrets) {
        if (!(form.shopify_client_id || '').trim()) errs.push('Shopify Client ID is required');
        if (!(form.shopify_client_secret || '').trim()) errs.push('Shopify Client secret is required');
    }
    return errs;
}

export default function ShopifyConnectFields({
    form,
    setForm,
    mode = 'create',
    hasExistingCredentials = false,
}) {
    const enabled = !!form.shopify_enabled;
    const secretHint = mode === 'edit'
        ? 'Leave Client ID / secret blank to keep the saved values.'
        : 'From Dev Dashboard → sellerpilothub → Settings.';

    return (
        <div className="sm:col-span-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50/80 dark:bg-slate-800/40 p-4 space-y-3">
            <div>
                <p className="text-sm font-medium text-slate-800 dark:text-slate-100">Connect Shopify?</p>
                <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                    Optional. New marketplace orders will also be created in Shopify Admin (same workflow as Omnivore).
                    Existing orders already in this app are not pushed. Turn off Omnivore order sync first to avoid duplicates.
                </p>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {[
                    { value: false, title: 'No', desc: 'Orders stay in this app only.' },
                    { value: true, title: 'Yes', desc: 'Also create new orders in Shopify.' },
                ].map((opt) => (
                    <button
                        key={String(opt.value)}
                        type="button"
                        onClick={() => setForm((f) => ({ ...f, shopify_enabled: opt.value }))}
                        className={`rounded-lg border p-3 text-left transition ${
                            enabled === opt.value
                                ? 'border-accent-500 bg-accent-50 dark:bg-accent-900/20 ring-1 ring-accent-500'
                                : 'border-slate-200 dark:border-slate-700 hover:border-slate-300 dark:hover:border-slate-600'
                        }`}
                    >
                        <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">{opt.title}</p>
                        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{opt.desc}</p>
                    </button>
                ))}
            </div>
            {enabled && (
                <div className="space-y-3 pt-1">
                    <Input
                        label="Shopify shop domain"
                        placeholder="your-store.myshopify.com"
                        value={form.shopify_shop_domain}
                        onChange={(e) => setForm((f) => ({ ...f, shopify_shop_domain: e.target.value }))}
                        required
                    />
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <Input
                            label="Client ID"
                            placeholder={mode === 'edit' && hasExistingCredentials ? 'Leave blank to keep current' : 'From Dev Dashboard Settings'}
                            value={form.shopify_client_id}
                            onChange={(e) => setForm((f) => ({ ...f, shopify_client_id: e.target.value }))}
                            required={mode === 'create' || !hasExistingCredentials}
                        />
                        <Input
                            label="Client secret"
                            type="password"
                            placeholder={mode === 'edit' && hasExistingCredentials ? 'Leave blank to keep current' : 'From Dev Dashboard Settings'}
                            value={form.shopify_client_secret}
                            onChange={(e) => setForm((f) => ({ ...f, shopify_client_secret: e.target.value }))}
                            required={mode === 'create' || !hasExistingCredentials}
                        />
                    </div>
                    <Input
                        label="Location ID (optional)"
                        placeholder="From Settings → Locations → open location → number in the URL"
                        value={form.shopify_location_id}
                        onChange={(e) => setForm((f) => ({ ...f, shopify_location_id: e.target.value }))}
                    />
                    <p className="text-xs text-slate-500 dark:text-slate-400">{secretHint}</p>
                </div>
            )}
        </div>
    );
}
