export default function Select({ label, options, className = '', ...props }) {
    return (
        <div className={className}>
            {label && (
                <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
                    {label}
                </label>
            )}
            <select
                className="block w-full rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 shadow-xs focus:border-accent-500 focus:ring-1 focus:ring-accent-500 px-3 py-2 text-sm outline-hidden"
                {...props}
            >
                {/* Options with a ``group`` are shown under that heading (consecutive options share one group). */}
                {options.reduce((out, opt) => {
                    const last = out[out.length - 1];
                    if (opt.group && last && last.group === opt.group) last.items.push(opt);
                    else out.push({ group: opt.group || '', items: [opt] });
                    return out;
                }, []).map((block, i) => {
                    const items = block.items.map((opt) => (
                        <option key={opt.value} value={opt.value}>
                            {opt.label}
                        </option>
                    ));
                    return block.group
                        ? <optgroup key={`${block.group}-${i}`} label={block.group}>{items}</optgroup>
                        : items;
                })}
            </select>
        </div>
    );
}
