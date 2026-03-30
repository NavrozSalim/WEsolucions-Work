/**
 * Status and label badges — professional, restrained.
 */
const variants = {
    default: 'bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300',
    success: 'bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400',
    warning: 'bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400',
    error: 'bg-rose-50 dark:bg-rose-900/30 text-rose-700 dark:text-rose-400',
    accent: 'bg-accent-50 dark:bg-accent-900/30 text-accent-700 dark:text-accent-400',
};

export default function Badge({ children, variant = 'default', className = '' }) {
    return (
        <span
            className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${variants[variant]} ${className}`}
        >
            {children}
        </span>
    );
}
