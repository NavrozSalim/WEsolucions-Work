import Input from '../ui/Input';
import Select from '../ui/Select';

/**
 * MyDeal / Woolworths (WMP) Universal API connection fields.
 * Shows sandbox or production credentials for the selected active environment.
 *
 * @param {'create'|'edit'} mode - create requires secrets for active env; edit leaves blank = keep current
 */
export default function MydealConnectionFields({ form, setForm, mode = 'edit' }) {
    const env = form.mydeal_environment || 'sandbox';
    const isCreate = mode === 'create';
    const isSandbox = env === 'sandbox';
    const prefix = isSandbox ? 'mydeal_sandbox' : 'mydeal_production';
    const envLabel = isSandbox ? 'Sandbox' : 'Production (Live)';

    const setField = (key, value) => setForm((f) => ({ ...f, [key]: value }));

    return (
        <div className="space-y-3 rounded-lg border border-slate-200 dark:border-slate-700 p-4">
            <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">
                MyDeal / Woolworths API connection
            </p>

            <Select
                label="Active environment"
                value={env}
                onChange={(e) => setField('mydeal_environment', e.target.value)}
                options={[
                    { value: 'sandbox', label: 'Sandbox (test first)' },
                    { value: 'production', label: 'Production (Live)' },
                ]}
            />

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 rounded-md border border-slate-100 dark:border-slate-700/80 bg-slate-50/60 dark:bg-slate-800/40 p-3">
                <p className="sm:col-span-2 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                    {envLabel} · active
                </p>
                <Input
                    label={`${envLabel} API base URL`}
                    placeholder="https://… (from MyDeal team)"
                    value={form[`${prefix}_base_url`] || ''}
                    onChange={(e) => setField(`${prefix}_base_url`, e.target.value)}
                    required
                    className="sm:col-span-2"
                />
                <Input
                    label="Client ID"
                    type="password"
                    placeholder={isCreate ? 'Sandbox/Live ClientID' : 'Leave blank to keep current'}
                    value={form[`${prefix}_client_id`] || ''}
                    onChange={(e) => setField(`${prefix}_client_id`, e.target.value)}
                    required={isCreate}
                />
                <Input
                    label="Client Secret"
                    type="password"
                    placeholder={isCreate ? 'Sandbox/Live ClientSecret' : 'Leave blank to keep current'}
                    value={form[`${prefix}_client_secret`] || ''}
                    onChange={(e) => setField(`${prefix}_client_secret`, e.target.value)}
                    required={isCreate}
                />
                <Input
                    label="Seller ID"
                    type="password"
                    placeholder={isCreate ? 'SellerID from MyDeal' : 'Leave blank to keep current'}
                    value={form[`${prefix}_seller_id`] || ''}
                    onChange={(e) => setField(`${prefix}_seller_id`, e.target.value)}
                    required={isCreate}
                />
                <Input
                    label="Seller Token"
                    type="password"
                    placeholder={isCreate ? 'SellerToken from MyDeal' : 'Leave blank to keep current'}
                    value={form[`${prefix}_seller_token`] || ''}
                    onChange={(e) => setField(`${prefix}_seller_token`, e.target.value)}
                    required={isCreate}
                />
            </div>

            <p className="text-xs text-slate-500 dark:text-slate-400">
                {isCreate
                    ? 'Use sandbox credentials from MyDeal first. Token is obtained via /mydealaccesstoken; SellerID and SellerToken are sent on every API call.'
                    : 'Secrets are write-only. Leave blank to keep existing values. Only the selected environment is used for connection tests and future syncs.'}
            </p>
        </div>
    );
}
