import Input from '../ui/Input';
import Select from '../ui/Select';

/**
 * Lasoo staging/production connection fields.
 * Shows only the AuthKey (and base URL) for the selected active environment.
 *
 * @param {'create'|'edit'} mode - create requires AuthKey for active env; edit leaves blank = keep current
 */
export default function LasooConnectionFields({ form, setForm, mode = 'edit' }) {
    const env = form.lasoo_environment || 'staging';
    const isCreate = mode === 'create';
    const isStaging = env === 'staging';

    return (
        <div className="space-y-3 rounded-lg border border-slate-200 dark:border-slate-700 p-4">
            <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">Lasoo connection</p>

            <Select
                label="Active environment"
                value={env}
                onChange={(e) => setForm((f) => ({ ...f, lasoo_environment: e.target.value }))}
                options={[
                    { value: 'staging', label: 'Staging (test first)' },
                    { value: 'production', label: 'Production' },
                ]}
            />

            {isStaging ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 rounded-md border border-slate-100 dark:border-slate-700/80 bg-slate-50/60 dark:bg-slate-800/40 p-3">
                    <p className="sm:col-span-2 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                        Staging · active
                    </p>
                    <Input
                        label="Staging base URL"
                        value={form.lasoo_staging_base_url}
                        onChange={(e) => setForm((f) => ({ ...f, lasoo_staging_base_url: e.target.value }))}
                        required
                    />
                    <Input
                        label="Staging AuthKey"
                        type="password"
                        placeholder={isCreate ? 'Provided by Lasoo' : 'Leave blank to keep current'}
                        value={form.lasoo_staging_auth_key}
                        onChange={(e) => setForm((f) => ({ ...f, lasoo_staging_auth_key: e.target.value }))}
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
                        value={form.lasoo_production_base_url}
                        onChange={(e) => setForm((f) => ({ ...f, lasoo_production_base_url: e.target.value }))}
                        required
                    />
                    <Input
                        label="Production AuthKey"
                        type="password"
                        placeholder={
                            isCreate
                                ? 'Provided by Lasoo'
                                : 'Leave blank to keep current'
                        }
                        value={form.lasoo_production_auth_key}
                        onChange={(e) => setForm((f) => ({ ...f, lasoo_production_auth_key: e.target.value }))}
                        required={isCreate}
                    />
                </div>
            )}

            <p className="text-xs text-slate-500 dark:text-slate-400">
                {isCreate
                    ? 'Listings and orders use the selected environment only. Switch Active environment to enter the other AuthKey when ready.'
                    : 'AuthKeys are write-only. Leave blank to keep the existing key. Only the selected environment is used for listings and orders.'}
            </p>
        </div>
    );
}
