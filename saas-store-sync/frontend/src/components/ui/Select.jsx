export default function Select({ label, options, className = '', ...props }) {
    return (
        <div className={className}>
            {label && (
                <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
                    {label}
                </label>
            )}
            <select
                className="block w-full rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 shadow-sm focus:border-accent-500 focus:ring-1 focus:ring-accent-500 px-3 py-2 text-sm outline-none"
                {...props}
            >
                {options.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                        {opt.label}
                    </option>
                ))}
            </select>
        </div>
    );
}
