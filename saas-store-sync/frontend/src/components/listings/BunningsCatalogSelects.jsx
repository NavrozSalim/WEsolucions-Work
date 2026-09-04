import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, Loader2, Search } from 'lucide-react';
import { getBunningsCategories, getBunningsLogistics } from '../../services/listingService';

/**
 * Searchable Bunnings (Mirakl H11) category picker. Value stored is the hierarchy code.
 */
export function BunningsCategorySelect({ storeId, value, onChange, required = false }) {
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState('');
    const [options, setOptions] = useState([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [labelByCode, setLabelByCode] = useState({});
    const rootRef = useRef(null);
    const debounceRef = useRef(null);

    const selectedLabel = useMemo(() => {
        if (!value) return '';
        if (labelByCode[value]) return labelByCode[value];
        const hit = options.find((o) => o.code === value);
        if (hit) return hit.name;
        return value;
    }, [value, labelByCode, options]);

    useEffect(() => {
        if (!open) return;
        const onDoc = (e) => {
            if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
        };
        document.addEventListener('mousedown', onDoc);
        return () => document.removeEventListener('mousedown', onDoc);
    }, [open]);

    useEffect(() => {
        if (!storeId || !open) return;
        clearTimeout(debounceRef.current);
        debounceRef.current = setTimeout(() => {
            setLoading(true);
            setError('');
            getBunningsCategories(storeId, query)
                .then((res) => {
                    const list = Array.isArray(res.data?.categories) ? res.data.categories : [];
                    setOptions(list);
                    setLabelByCode((prev) => {
                        const next = { ...prev };
                        list.forEach((c) => {
                            if (c.code && c.name) next[c.code] = c.name;
                        });
                        return next;
                    });
                })
                .catch((err) => {
                    setError(err.response?.data?.detail || 'Could not load Bunnings categories.');
                    setOptions([]);
                })
                .finally(() => setLoading(false));
        }, 250);
        return () => clearTimeout(debounceRef.current);
    }, [storeId, open, query]);

    return (
        <div ref={rootRef} className="relative sm:col-span-2">
            <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
                Category {required ? <span className="text-rose-500">*</span> : null}
            </label>
            <button
                type="button"
                onClick={() => setOpen((o) => !o)}
                className="flex w-full items-center justify-between rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-2 text-left text-sm text-slate-900 dark:text-slate-100"
            >
                <span className={selectedLabel ? '' : 'text-slate-400'}>
                    {selectedLabel || 'Search Bunnings categories…'}
                </span>
                <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" />
            </button>

            {open && (
                <div className="absolute z-40 mt-1 w-full overflow-hidden rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 shadow-lg">
                    <div className="flex items-center gap-2 border-b border-slate-200 dark:border-slate-700 px-3 py-2">
                        <Search className="h-4 w-4 text-slate-400" />
                        <input
                            autoFocus
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                            placeholder="Type a category name or code"
                            className="w-full bg-transparent text-sm outline-none text-slate-900 dark:text-slate-100"
                        />
                        {loading && <Loader2 className="h-4 w-4 animate-spin text-slate-400" />}
                    </div>
                    <div className="max-h-56 overflow-y-auto">
                        {error && (
                            <p className="px-3 py-2 text-xs text-rose-600 dark:text-rose-400">{error}</p>
                        )}
                        {!error && !loading && options.length === 0 && (
                            <p className="px-3 py-2 text-xs text-slate-500">No categories match.</p>
                        )}
                        {options.map((opt) => (
                            <button
                                key={opt.code}
                                type="button"
                                className={`block w-full px-3 py-2 text-left text-sm hover:bg-slate-50 dark:hover:bg-slate-800 ${
                                    value === opt.code ? 'bg-accent-50 dark:bg-accent-900/20 text-accent-700 dark:text-accent-300' : 'text-slate-800 dark:text-slate-200'
                                }`}
                                onClick={() => {
                                    onChange?.(opt.code, opt.name);
                                    setLabelByCode((prev) => ({ ...prev, [opt.code]: opt.name }));
                                    setOpen(false);
                                    setQuery('');
                                }}
                            >
                                {opt.name}
                                {opt.leaf === false ? (
                                    <span className="ml-1 text-xs text-slate-400">(parent)</span>
                                ) : null}
                            </button>
                        ))}
                    </div>
                </div>
            )}
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                Pick the Bunnings hierarchy code. Bulk CSV uses the same codes.
            </p>
        </div>
    );
}

/** Logistic class dropdown loaded from Bunnings shop shipping settings. */
export function BunningsLogisticSelect({ storeId, value, onChange, required = false }) {
    const [options, setOptions] = useState([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        if (!storeId) return;
        setLoading(true);
        getBunningsLogistics(storeId)
            .then((res) => {
                setOptions(Array.isArray(res.data?.classes) ? res.data.classes : []);
            })
            .catch((err) => {
                setError(err.response?.data?.detail || 'Could not load logistic classes.');
                setOptions([]);
            })
            .finally(() => setLoading(false));
    }, [storeId]);

    if (!loading && options.length === 0) {
        return (
            <div>
                <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
                    Logistic Class {required ? <span className="text-rose-500">*</span> : null}
                </label>
                <input
                    value={value || ''}
                    onChange={(e) => onChange?.(e.target.value)}
                    required={required}
                    placeholder="e.g. SMALL"
                    className="block w-full rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 shadow-sm focus:border-accent-500 focus:ring-1 focus:ring-accent-500 px-3 py-2 text-sm outline-none"
                />
                <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                    {error || 'Enter the Mirakl logistic class code from shop settings.'}
                </p>
            </div>
        );
    }

    const selectOptions = [
        { value: '', label: loading ? 'Loading…' : 'Select logistic class' },
        ...options.map((c) => ({ value: c.code, label: c.name || c.code })),
        ...(value && !options.some((c) => c.code === value)
            ? [{ value, label: value }]
            : []),
    ];

    return (
        <div>
            <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
                Logistic Class {required ? <span className="text-rose-500">*</span> : null}
            </label>
            <select
                value={value || ''}
                onChange={(e) => {
                    const code = e.target.value;
                    const hit = options.find((c) => c.code === code);
                    onChange?.(code, hit?.name || code);
                }}
                required={required}
                className="block w-full rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 shadow-sm focus:border-accent-500 focus:ring-1 focus:ring-accent-500 px-3 py-2 text-sm outline-none"
            >
                {selectOptions.map((opt) => (
                    <option key={opt.value || 'empty'} value={opt.value} disabled={!opt.value}>
                        {opt.label}
                    </option>
                ))}
            </select>
            {error && <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">{error}</p>}
        </div>
    );
}
