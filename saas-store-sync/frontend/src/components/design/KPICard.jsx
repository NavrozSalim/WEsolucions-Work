/**
 * KPI metric card — clean, Stripe-style.
 */
export default function KPICard({ label, value, sub, icon: Icon }) {
    return (
        <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-5">
            <div className="flex items-start justify-between">
                <div>
                    <p className="text-sm font-medium text-slate-500 dark:text-slate-400">{label}</p>
                    <p className="mt-1 text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100">
                        {value}
                    </p>
                    {sub && (
                        <p className="mt-0.5 text-xs text-slate-400 dark:text-slate-500">{sub}</p>
                    )}
                </div>
                {Icon && (
                    <div className="rounded-md bg-slate-100 dark:bg-slate-800 p-2">
                        <Icon className="h-5 w-5 text-slate-500 dark:text-slate-400" />
                    </div>
                )}
            </div>
        </div>
    );
}
