import Input from '../ui/Input';
import Select from '../ui/Select';

/**
 * Bunnings Marketplace (Mirakl Seller API) connection fields.
 * Auth: Authorization header with SHOP_KEY from Mirakl → My user settings → API Key.
 *
 * @param {'create'|'edit'} mode - create requires SHOP_KEY for active env; edit leaves blank = keep current
 */
export default function BunningsConnectionFields({ form, setForm, mode = 'edit' }) {
    const env = form.bunnings_environment || 'production';
    const isCreate = mode === 'create';
    const isStaging = env === 'staging';

    return (
        <div className="space-y-3 rounded-lg border border-slate-200 dark:border-slate-700 p-4">
            <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">Bunnings connection</p>

            <Select
                label="Active environment"
                value={env}
                onChange={(e) => setForm((f) => ({ ...f, bunnings_environment: e.target.value }))}
                options={[
                    { value: 'production', label: 'Production' },
                    { value: 'staging', label: 'Staging (optional test)' },
                ]}
            />

            {isStaging ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 rounded-md border border-slate-100 dark:border-slate-700/80 bg-slate-50/60 dark:bg-slate-800/40 p-3">
                    <p className="sm:col-span-2 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                        Staging · active
                    </p>
                    <Input
                        label="Staging base URL"
                        value={form.bunnings_staging_base_url}
                        onChange={(e) => setForm((f) => ({ ...f, bunnings_staging_base_url: e.target.value }))}
                        placeholder="https://bunnings-staging.mirakl.net"
                    />
                    <Input
                        label="Staging SHOP_KEY"
                        type="password"
                        placeholder={isCreate ? 'From Mirakl → My user settings → API Key' : 'Leave blank to keep current'}
                        value={form.bunnings_staging_shop_key}
                        onChange={(e) => setForm((f) => ({ ...f, bunnings_staging_shop_key: e.target.value }))}
                        required={isCreate}
                    />
                </div>
            ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 rounded-md border border-slate-100 dark:border-slate-700/80 bg-slate-50/60 dark:bg-slate-800/40 p-3">
                    <p className="sm:col-span-2 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                        Production · active
                    </p>
                    <Input
                        label="Production base URL"
                        value={form.bunnings_production_base_url}
                        onChange={(e) => setForm((f) => ({ ...f, bunnings_production_base_url: e.target.value }))}
                        required
                    />
                    <Input
                        label="Production SHOP_KEY"
                        type="password"
                        placeholder={isCreate ? 'From Mirakl → My user settings → API Key' : 'Leave blank to keep current'}
                        value={form.bunnings_production_shop_key}
                        onChange={(e) => setForm((f) => ({ ...f, bunnings_production_shop_key: e.target.value }))}
                        required={isCreate}
                    />
                </div>
            )}

            <p className="text-xs text-slate-500 dark:text-slate-400">
                {isCreate
                    ? 'Connection is tested with GET /api/hierarchies (H11). Use the Mirakl seller API key — no Bearer prefix.'
                    : 'SHOP_KEY is write-only. Leave blank to keep the existing key. Only the selected environment is used for listings and orders.'}
            </p>
        </div>
    );
}
