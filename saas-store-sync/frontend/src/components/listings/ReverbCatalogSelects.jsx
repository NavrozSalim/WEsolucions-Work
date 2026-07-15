import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, Loader2, Search } from 'lucide-react';
import { getReverbCategories, getReverbConditions } from '../../services/listingService';

/**
 * Searchable Reverb category picker. Value stored is the category UUID (or name if unresolved).
 * Shows Reverb full_name labels like "Accessories / Cables".
 */
export function ReverbCategorySelect({ storeId, value, onChange, required = false }) {
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState('');
    const [options, setOptions] = useState([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [labelByUuid, setLabelByUuid] = useState({});
    const rootRef = useRef(null);
    const debounceRef = useRef(null);

    const selectedLabel = useMemo(() => {
        if (!value) return '';
        if (labelByUuid[value]) return labelByUuid[value];
        const hit = options.find((o) => o.uuid === value);
        if (hit) return hit.name;
        // value may already be a human name from CSV
        return value;
    }, [value, labelByUuid, options]);

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
            getReverbCategories(storeId, query)
                .then((res) => {
                    const list = Array.isArray(res.data?.categories) ? res.data.categories : [];
                    setOptions(list);
                    setLabelByUuid((prev) => {
                        const next = { ...prev };
                        list.forEach((c) => {
                            if (c.uuid && c.name) next[c.uuid] = c.name;
                        });
                        return next;
                    });
                })
                .catch((err) => {
                    setError(err.response?.data?.detail || 'Could not load categories.');
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
                    {selectedLabel || 'Search Reverb categories…'}
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
                            placeholder="Type to search (e.g. Cables, Pedals)"
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
                                key={opt.uuid}
                                type="button"
                                className={`block w-full px-3 py-2 text-left text-sm hover:bg-slate-50 dark:hover:bg-slate-800 ${
                                    value === opt.uuid ? 'bg-accent-50 dark:bg-accent-900/20 text-accent-700 dark:text-accent-300' : 'text-slate-800 dark:text-slate-200'
                                }`}
                                onClick={() => {
                                    onChange?.(opt.uuid, opt.name);
                                    setLabelByUuid((prev) => ({ ...prev, [opt.uuid]: opt.name }));
                                    setOpen(false);
                                    setQuery('');
                                }}
                            >
                                {opt.name}
                            </button>
                        ))}
                    </div>
                </div>
            )}
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                Pick the exact Reverb category name. Bulk CSV can use the same names.
            </p>
        </div>
    );
}

/** Condition dropdown loaded from Reverb listing_conditions. */
export function ReverbConditionSelect({ storeId, value, onChange, required = false }) {
    const [options, setOptions] = useState([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        if (!storeId) return;
        setLoading(true);
        getReverbConditions(storeId)
            .then((res) => {
                setOptions(Array.isArray(res.data?.conditions) ? res.data.conditions : []);
            })
            .catch((err) => {
                setError(err.response?.data?.detail || 'Could not load conditions.');
                setOptions([]);
            })
            .finally(() => setLoading(false));
    }, [storeId]);

    const selectOptions = [
        { value: '', label: loading ? 'Loading…' : 'Select condition' },
        ...options.map((c) => ({ value: c.uuid, label: c.name })),
        // Keep current free-text / name if not in list yet
        ...(value && !options.some((c) => c.uuid === value)
            ? [{ value, label: value }]
            : []),
    ];

    return (
        <div>
            <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
                Condition {required ? <span className="text-rose-500">*</span> : null}
            </label>
            <select
                value={value || ''}
                onChange={(e) => {
                    const uuid = e.target.value;
                    const hit = options.find((c) => c.uuid === uuid);
                    onChange?.(uuid, hit?.name || uuid);
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
