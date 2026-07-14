import { useEffect, useState } from 'react';
import Input from '../ui/Input';
import Select from '../ui/Select';

/**
 * Lasoo staging/production connection fields.
 * Shows only the active environment by default; optional toggle reveals the other.
 *
 * @param {'create'|'edit'} mode - create requires staging AuthKey; edit leaves blank = keep current
 */
export default function LasooConnectionFields({ form, setForm, mode = 'edit' }) {
    const env = form.lasoo_environment || 'staging';
    const [showOtherEnv, setShowOtherEnv] = useState(false);

    useEffect(() => {
        setShowOtherEnv(false);
    }, [env]);

    const showStaging = env === 'staging' || showOtherEnv;
    const showProduction = env === 'production' || showOtherEnv;
    const otherLabel = env === 'staging' ? 'production' : 'staging';
    const isCreate = mode === 'create';

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

            {showStaging && (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 rounded-md border border-slate-100 dark:border-slate-700/80 bg-slate-50/60 dark:bg-slate-800/40 p-3">
                    <p className="sm:col-span-2 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                        Staging
                        {env === 'staging' ? ' · active' : ''}
                    </p>
                    <Input
                        label="Staging base URL"
                        value={form.lasoo_staging_base_url}
                        onChange={(e) => setForm((f) => ({ ...f, lasoo_staging_base_url: e.target.value }))}
                        required={env === 'staging'}
                    />
                    <Input
                        label="Staging AuthKey"
                        type="password"
                        placeholder={isCreate ? 'Provided by Lasoo' : 'Leave blank to keep current'}
                        value={form.lasoo_staging_auth_key}
                        onChange={(e) => setForm((f) => ({ ...f, lasoo_staging_auth_key: e.target.value }))}
                        required={isCreate && env === 'staging'}
                    />
                </div>
            )}

            {showProduction && (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 rounded-md border border-slate-100 dark:border-slate-700/80 bg-slate-50/60 dark:bg-slate-800/40 p-3">
                    <p className="sm:col-span-2 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                        Production
                        {env === 'production' ? ' · active' : ''}
                    </p>
                    <Input
                        label="Production base URL"
                        value={form.lasoo_production_base_url}
                        onChange={(e) => setForm((f) => ({ ...f, lasoo_production_base_url: e.target.value }))}
                        required={env === 'production'}
                    />
                    <Input
                        label="Production AuthKey"
                        type="password"
                        placeholder={
                            isCreate
                                ? 'Add later once staging is verified'
                                : 'Leave blank to keep current'
                        }
                        value={form.lasoo_production_auth_key}
                        onChange={(e) => setForm((f) => ({ ...f, lasoo_production_auth_key: e.target.value }))}
                        required={isCreate && env === 'production'}
                    />
                </div>
            )}

            <button
                type="button"
                className="text-xs font-medium text-accent-600 hover:underline dark:text-accent-400"
                onClick={() => setShowOtherEnv((v) => !v)}
            >
                {showOtherEnv
                    ? `Hide ${otherLabel} settings`
                    : `Also configure ${otherLabel}`}
            </button>

            <p className="text-xs text-slate-500 dark:text-slate-400">
                {isCreate
                    ? 'Listings and orders use the active environment. Start with staging, then switch to production once Lasoo approves your feed.'
                    : 'AuthKeys are write-only. Leave blank to keep existing keys. Listings and orders use the active environment.'}
            </p>
        </div>
    );
}
