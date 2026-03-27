export default function Input({ label, error, className = '', ...props }) {
    return (
        <div className={className}>
            {label && (
                <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
                    {label}
                </label>
            )}
            <input
                className={`block w-full rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 shadow-sm focus:border-accent-500 focus:ring-1 focus:ring-accent-500 px-3 py-2 text-sm outline-none ${error ? 'border-rose-500' : ''}`}
                {...props}
            />
            {error && <p className="mt-1 text-sm text-rose-600 dark:text-rose-400">{error}</p>}
        </div>
    );
}
