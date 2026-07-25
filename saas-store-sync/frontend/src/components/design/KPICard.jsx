import { Link } from 'react-router-dom';

/**
 * KPI metric card — clean, Stripe-style. Optional `to` makes the whole card a link.
 */
export default function KPICard({ label, value, sub, icon: Icon, to, tone = 'default' }) {
    const toneIcon = {
        default: 'bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400',
        warning: 'bg-amber-50 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400',
        error: 'bg-rose-50 dark:bg-rose-900/30 text-rose-600 dark:text-rose-400',
        success: 'bg-emerald-50 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400',
        accent: 'bg-accent-50 dark:bg-accent-900/30 text-accent-600 dark:text-accent-400',
    }[tone] || 'bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400';

    const className = [
        'block rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-5',
        'transition-colors',
        to
            ? 'hover:border-accent-400 dark:hover:border-accent-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500'
            : '',
    ]
        .filter(Boolean)
        .join(' ');

    const content = (
        <div className="flex items-start justify-between">
            <div>
                <p className="text-sm font-medium text-slate-500 dark:text-slate-400">{label}</p>
                <p className="mt-1 text-2xl font-semibold tracking-tight tabular-nums text-slate-900 dark:text-slate-100">
                    {value}
                </p>
                {sub && (
                    <p className="mt-0.5 text-xs text-slate-400 dark:text-slate-500">{sub}</p>
                )}
            </div>
            {Icon && (
                <div className={`rounded-md p-2 ${toneIcon}`}>
                    <Icon className="h-5 w-5" />
                </div>
            )}
        </div>
    );

    if (to) {
        return (
            <Link to={to} className={className}>
                {content}
            </Link>
        );
    }

    return <div className={className}>{content}</div>;
}
