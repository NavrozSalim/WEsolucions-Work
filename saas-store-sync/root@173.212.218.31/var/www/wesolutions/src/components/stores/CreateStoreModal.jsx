import { useState, useEffect } from 'react';
import { X, Plus, Clock, Trash2 } from 'lucide-react';
import Button from '../ui/Button';
import Input from '../ui/Input';
import Select from '../ui/Select';
import { createStore, getMarketplaces, getVendors } from '../../services/storeService';
import { validateVendorPriceSettings } from '../../utils/priceRangeValidation';

const emptyPriceRange = () => ({ from_value: 0, to_value: null, margin_type: 'percentage', margin_percentage: 25 });
const emptyInventoryRange = () => ({ from_value: 0, to_value: 999999999, range_type: 'multiplier', multiplier: 0.5, fixed_value: null });

const TIMEZONE_OPTIONS = {
    USA: [
        { value: 'America/New_York', label: 'Eastern (New York)' },
        { value: 'America/Chicago', label: 'Central (Chicago)' },
        { value: 'America/Denver', label: 'Mountain (Denver)' },
        { value: 'America/Los_Angeles', label: 'Pacific (Los Angeles)' },
    ],
    AU: [
        { value: 'Australia/Sydney', label: 'Sydney (AEST)' },
        { value: 'Australia/Melbourne', label: 'Melbourne (AEST)' },
        { value: 'Australia/Perth', label: 'Perth (AWST)' },
        { value: 'Australia/Brisbane', label: 'Brisbane (AEST)' },
    ],
};

const FREQUENCY_OPTIONS = [
    { value: 'daily', label: 'Daily' },
    { value: 'every_12h', label: 'Every 12 hours' },
    { value: 'every_6h', label: 'Every 6 hours' },
    { value: 'every_2h', label: 'Every 2 hours' },
];

const DEFAULT_TZ = { USA: 'America/New_York', AU: 'Australia/Sydney' };

function frequencyToCrontab(freq, hour, minute) {
    const h = parseInt(hour, 10) || 0;
    const m = parseInt(minute, 10) || 0;
    switch (freq) {
        case 'every_2h': return { schedule_type: 'crontab', crontab_hour: '*/2', crontab_minute: String(m) };
        case 'every_6h': return { schedule_type: 'crontab', crontab_hour: '*/6', crontab_minute: String(m) };
        case 'every_12h': return { schedule_type: 'crontab', crontab_hour: '*/12', crontab_minute: String(m) };
        default: return { schedule_type: 'crontab', crontab_hour: String(h), crontab_minute: String(m) };
    }
}

export default function CreateStoreModal({ open, onClose, onSuccess, copyFromStore = null, marketplaces: extMarketplaces = [] }) {
    const isDuplicate = !!copyFromStore;
    const [step, setStep] = useState(1);
    const [marketplaces, setMarketplaces] = useState([]);
    const [vendors, setVendors] = useState([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [selectedVendorPrice, setSelectedVendorPrice] = useState('');
    const [selectedVendorInventory, setSelectedVendorInventory] = useState('');
    const [form, setForm] = useState({
        name: '',
        marketplace_id: '',
        api_token: '',
        region: 'USA',
        vendor_price_settings: [],
        vendor_inventory_settings: [],
        schedule_enabled: false,
        schedule_frequency: 'daily',
        schedule_hour: '10',
        schedule_minute: '00',
        schedule_timezone: 'America/New_York',
    });

    useEffect(() => {
        if (open) {
            if (extMarketplaces.length) setMarketplaces(extMarketplaces);
            else getMarketplaces().then((r) => setMarketplaces(Array.isArray(r.data) ? r.data : []));
            if (!isDuplicate) getVendors().then((r) => setVendors(Array.isArray(r.data) ? r.data : []));
            setStep(1);
            const region = copyFromStore?.region || 'USA';
            setForm({
                name: copyFromStore ? `${copyFromStore.name} (Copy)` : '',
                marketplace_id: copyFromStore?.marketplace_id || copyFromStore?.marketplace || '',
                api_token: '',
                region,
                vendor_price_settings: [],
                vendor_inventory_settings: [],
                schedule_enabled: false,
                schedule_frequency: 'daily',
                schedule_hour: '10',
                schedule_minute: '00',
                schedule_timezone: DEFAULT_TZ[region] || 'America/New_York',
            });
            setSelectedVendorPrice('');
            setSelectedVendorInventory('');
            setError('');
        }
    }, [open, copyFromStore, isDuplicate, extMarketplaces.length]);

    const regionTimezones = TIMEZONE_OPTIONS[form.region] || TIMEZONE_OPTIONS.USA;

    const usedPriceVendorIds = (form.vendor_price_settings || []).map((v) => v.vendor_id).filter(Boolean);
    const usedInventoryVendorIds = (form.vendor_inventory_settings || []).map((v) => v.vendor_id).filter(Boolean);

    const addVendorPrice = (vendorId) => {
        const vid = vendorId || selectedVendorPrice;
        if (!vid) {
            setForm((f) => ({
                ...f,
                vendor_price_settings: [...(f.vendor_price_settings || []), { vendor_id: '', purchase_tax_percentage: 0, marketplace_fees_percentage: 0, rounding_option: 'none', range_margins: [emptyPriceRange()] }],
            }));
            return;
        }
        setSelectedVendorPrice('');
        if (vid === '__all__') {
            const toAdd = vendors.filter((v) => !usedPriceVendorIds.includes(v.id)).map((v) => ({ vendor_id: v.id, purchase_tax_percentage: 0, marketplace_fees_percentage: 0, rounding_option: 'none', range_margins: [emptyPriceRange()] }));
            if (toAdd.length === 0) return;
            setForm((f) => ({ ...f, vendor_price_settings: [...(f.vendor_price_settings || []), ...toAdd] }));
            return;
        }
        if (usedPriceVendorIds.includes(vid)) return;
        setForm((f) => ({
            ...f,
            vendor_price_settings: [...(f.vendor_price_settings || []), { vendor_id: vid, purchase_tax_percentage: 0, marketplace_fees_percentage: 0, rounding_option: 'none', range_margins: [emptyPriceRange()] }],
        }));
    };
    const removeVendorPrice = (i) => setForm((f) => ({ ...f, vendor_price_settings: (f.vendor_price_settings || []).filter((_, idx) => idx !== i) }));
    const updateVendorPrice = (i, field, value) => {
        setForm((f) => {
            const next = [...(f.vendor_price_settings || [])];
            next[i] = { ...next[i], [field]: value };
            return { ...f, vendor_price_settings: next };
        });
    };
    const addPriceRange = (vendorIdx) => {
        setForm((f) => {
            const next = [...(f.vendor_price_settings || [])];
            const margins = [...(next[vendorIdx].range_margins || [])];
            const last = margins[margins.length - 1];
            let nextFrom = 0;
            if (last && last.to_value !== '' && last.to_value != null && String(last.to_value).trim().toUpperCase() !== 'MAX') {
                const t = parseFloat(String(last.to_value));
                if (Number.isFinite(t)) nextFrom = t + 1;
            }
            margins.push({ from_value: nextFrom, to_value: null, margin_type: 'percentage', margin_percentage: 25 });
            next[vendorIdx] = { ...next[vendorIdx], range_margins: margins };
            return { ...f, vendor_price_settings: next };
        });
    };
    const removePriceRange = (vendorIdx, rangeIdx) => {
        setForm((f) => {
            const next = [...(f.vendor_price_settings || [])];
            const ranges = (next[vendorIdx].range_margins || []).filter((_, i) => i !== rangeIdx);
            next[vendorIdx] = { ...next[vendorIdx], range_margins: ranges.length ? ranges : [emptyPriceRange()] };
            return { ...f, vendor_price_settings: next };
        });
    };
    const updatePriceRange = (vendorIdx, rangeIdx, field, value) => {
        setForm((f) => {
            const next = [...(f.vendor_price_settings || [])];
            const ranges = [...(next[vendorIdx].range_margins || [])];
            ranges[rangeIdx] = { ...ranges[rangeIdx], [field]: value };
            next[vendorIdx] = { ...next[vendorIdx], range_margins: ranges };
            return { ...f, vendor_price_settings: next };
        });
    };

    const addVendorInventory = (vendorId) => {
        const vid = vendorId || selectedVendorInventory;
        if (!vid) {
            setForm((f) => ({
                ...f,
                vendor_inventory_settings: [...(f.vendor_inventory_settings || []), { vendor_id: '', range_multipliers: [emptyInventoryRange()] }],
            }));
            return;
        }
        setSelectedVendorInventory('');
        if (vid === '__all__') {
            const toAdd = vendors.filter((v) => !usedInventoryVendorIds.includes(v.id)).map((v) => ({ vendor_id: v.id, range_multipliers: [emptyInventoryRange()] }));
            if (toAdd.length === 0) return;
            setForm((f) => ({ ...f, vendor_inventory_settings: [...(f.vendor_inventory_settings || []), ...toAdd] }));
            return;
        }
        if (usedInventoryVendorIds.includes(vid)) return;
        setForm((f) => ({
            ...f,
            vendor_inventory_settings: [...(f.vendor_inventory_settings || []), { vendor_id: vid, range_multipliers: [emptyInventoryRange()] }],
        }));
    };
    const removeVendorInventory = (i) => setForm((f) => ({ ...f, vendor_inventory_settings: (f.vendor_inventory_settings || []).filter((_, idx) => idx !== i) }));
    const updateVendorInventory = (i, field, value) => {
        setForm((f) => {
            const next = [...(f.vendor_inventory_settings || [])];
            next[i] = { ...next[i], [field]: value };
            return { ...f, vendor_inventory_settings: next };
        });
    };
    const addInventoryRange = (vendorIdx) => {
        setForm((f) => {
            const next = [...(f.vendor_inventory_settings || [])];
            next[vendorIdx] = { ...next[vendorIdx], range_multipliers: [...(next[vendorIdx].range_multipliers || []), emptyInventoryRange()] };
            return { ...f, vendor_inventory_settings: next };
        });
    };
    const removeInventoryRange = (vendorIdx, rangeIdx) => {
        setForm((f) => {
            const next = [...(f.vendor_inventory_settings || [])];
            const ranges = (next[vendorIdx].range_multipliers || []).filter((_, i) => i !== rangeIdx);
            next[vendorIdx] = { ...next[vendorIdx], range_multipliers: ranges.length ? ranges : [emptyInventoryRange()] };
            return { ...f, vendor_inventory_settings: next };
        });
    };
    const updateInventoryRange = (vendorIdx, rangeIdx, field, value) => {
        setForm((f) => {
            const next = [...(f.vendor_inventory_settings || [])];
            const ranges = [...(next[vendorIdx].range_multipliers || [])];
            ranges[rangeIdx] = { ...ranges[rangeIdx], [field]: value };
            next[vendorIdx] = { ...next[vendorIdx], range_multipliers: ranges };
            return { ...f, vendor_inventory_settings: next };
        });
    };

    const validateStep1 = () => {
        const errs = [];
        if (!form.name?.trim()) errs.push('Store name is required');
        if (!form.marketplace_id) errs.push('Marketplace is required');
        if (!form.api_token?.trim()) errs.push('API key is required');
        return errs;
    };
    const validateStep2 = () => {
        const errs = [];
        if (!(form.vendor_price_settings || []).some((vp) => vp.vendor_id)) errs.push('Add at least one vendor with price settings');
        (form.vendor_price_settings || []).forEach((vp) => {
            if (!vp.vendor_id) return;
            const allDirect = (vp.range_margins || []).length > 0 && (vp.range_margins || []).every((r) => r.margin_type === 'direct');
            if (!allDirect) {
                if (vp.purchase_tax_percentage === '' || vp.purchase_tax_percentage == null) errs.push('Purchase tax % is required');
                if (vp.marketplace_fees_percentage === '' || vp.marketplace_fees_percentage == null) errs.push('Marketplace fees % is required');
                const pt = parseFloat(vp.purchase_tax_percentage);
                const mf = parseFloat(vp.marketplace_fees_percentage);
                if (!Number.isFinite(pt) || pt < 0) errs.push('Purchase tax % must be a non‑negative number.');
                if (!Number.isFinite(mf) || mf < 0) errs.push('Marketplace fees % must be a non‑negative number.');
            }
        });
        errs.push(...validateVendorPriceSettings(form.vendor_price_settings));
        return errs;
    };
    const validateStep3 = () => {
        const errs = [];
        if (!(form.vendor_inventory_settings || []).some((vi) => vi.vendor_id)) errs.push('Add at least one vendor with inventory ranges');
        return errs;
    };

    const buildSettingsPayload = () => ({
        vendor_price_settings: (form.vendor_price_settings || []).map((vp) => {
            const ranges = vp.range_margins || [];
            const allDirect = ranges.length > 0 && ranges.every((r) => r.margin_type === 'direct');
            return {
                vendor_id: vp.vendor_id,
                purchase_tax_percentage: allDirect ? 0 : (parseFloat(vp.purchase_tax_percentage) || 0),
                marketplace_fees_percentage: allDirect ? 0 : (parseFloat(vp.marketplace_fees_percentage) || 0),
                rounding_option: vp.rounding_option || 'none',
                range_margins: ranges.map((r) => ({
                    from_value: parseFloat(r.from_value) || 0,
                    to_value: r.to_value === '' || r.to_value === 'MAX' ? null : parseFloat(r.to_value),
                    margin_type: r.margin_type || 'percentage',
                    margin_percentage: parseFloat(r.margin_percentage) || 0,
                })),
            };
        }).filter((vp) => vp.vendor_id),
        vendor_inventory_settings: (form.vendor_inventory_settings || []).map((vi) => ({
            vendor_id: vi.vendor_id ? String(vi.vendor_id) : undefined,
            range_multipliers: (vi.range_multipliers || []).map((r) => ({
                from_value: parseFloat(r.from_value) || 0,
                to_value: r.to_value === '' || r.to_value === 'MAX' ? null : parseFloat(r.to_value),
                range_type: r.range_type || 'multiplier',
                multiplier: parseFloat(r.multiplier) || 1,
                fixed_value: r.range_type === 'fixed' ? (parseInt(r.fixed_value, 10) ?? 0) : null,
            })),
        })).filter((vi) => vi.vendor_id),
    });

    const handleNext = () => {
        if (step === 1) {
            const errs = validateStep1();
            if (errs.length) { setError(errs.join('. ')); return; }
            setError('');
            setStep(2);
        } else if (step === 2) {
            const errs = validateStep2();
            if (errs.length) { setError(errs.join('. ')); return; }
            setError('');
            const priceIds = (form.vendor_price_settings || []).map((v) => v.vendor_id).filter(Boolean);
            const invIds = (form.vendor_inventory_settings || []).map((v) => v.vendor_id).filter(Boolean);
            const toAdd = priceIds.filter((id) => !invIds.includes(id));
            if (toAdd.length > 0) {
                setForm((f) => ({
                    ...f,
                    vendor_inventory_settings: [
                        ...(f.vendor_inventory_settings || []),
                        ...toAdd.map((vid) => ({ vendor_id: vid, range_multipliers: [emptyInventoryRange()] })),
                    ],
                }));
            }
            setStep(3);
        }
    };

    const buildSchedulePayload = () => {
        if (!form.schedule_enabled) return null;
        const cron = frequencyToCrontab(form.schedule_frequency, form.schedule_hour, form.schedule_minute);
        return {
            enabled: true,
            schedule_type: cron.schedule_type,
            crontab_hour: cron.crontab_hour,
            crontab_minute: cron.crontab_minute,
            crontab_day_of_week: '*',
            timezone: form.schedule_timezone,
        };
    };

    const handleSubmit = (e) => {
        e.preventDefault();
        if (isDuplicate) {
            const errs = validateStep1();
            if (errs.length) { setError(errs.join('. ')); return; }
            setLoading(true);
            setError('');
            const schedPayload = buildSchedulePayload();
            const payload = {
                name: form.name.trim(),
                marketplace_id: form.marketplace_id,
                api_token: form.api_token,
                region: form.region,
                ...(schedPayload ? { sync_schedule: schedPayload } : {}),
            };
            if (copyFromStore?.vendor_price_settings?.length || copyFromStore?.vendor_inventory_settings?.length) {
                payload.vendor_price_settings = (copyFromStore.vendor_price_settings || []).map((vp) => ({
                    vendor_id: vp.vendor || vp.vendor_id,
                    purchase_tax_percentage: vp.purchase_tax_percentage ?? 0,
                    marketplace_fees_percentage: vp.marketplace_fees_percentage ?? 0,
                    multiplier: vp.multiplier ?? 1,
                    optional_fee: vp.optional_fee ?? 0,
                    rounding_option: vp.rounding_option || 'none',
                    range_margins: (vp.range_margins || []).map((r) => ({
                        from_value: r.from_value ?? 0,
                        to_value: r.to_value ?? null,
                        margin_type: r.margin_type || 'percentage',
                        margin_percentage: parseFloat(r.margin_percentage) || 0,
                    })),
                })).filter((vp) => vp.vendor_id);
                payload.vendor_inventory_settings = (copyFromStore.vendor_inventory_settings || []).map((vi) => ({
                    vendor_id: vi.vendor || vi.vendor_id,
                    rule_type: vi.rule_type || 'multiplier',
                    default_multiplier: vi.default_multiplier ?? 1,
                    default_value: vi.default_value ?? 1,
                    zero_if_low: vi.zero_if_low !== false,
                    range_multipliers: (vi.range_multipliers || []).map((r) => ({
                        from_value: r.from_value ?? 0,
                        to_value: r.to_value ?? null,
                        range_type: r.range_type || 'multiplier',
                        multiplier: r.multiplier ?? 0.5,
                        fixed_value: r.fixed_value ?? null,
                    })),
                })).filter((vi) => vi.vendor_id && (vi.range_multipliers?.length || 0) > 0);
            }
            createStore(payload)
                .then(() => { onSuccess(); onClose(); })
                .catch((err) => {
                    const d = err.response?.data;
                    let msg = d?.detail;
                    if (!msg && typeof d === 'object') {
                        const parts = Object.entries(d).flatMap(([k, v]) =>
                            (Array.isArray(v) ? v : [v]).map((x) => (typeof x === 'string' ? x : `${k}: ${JSON.stringify(x)}`))
                        );
                        msg = parts.length ? parts.join('. ') : null;
                    }
                    setError(msg || 'Failed to create store');
                })
                .finally(() => setLoading(false));
            return;
        }

        if (step < 3) {
            handleNext();
            return;
        }

        const errs = validateStep3();
        if (errs.length) { setError(errs.join('. ')); return; }
        setLoading(true);
        setError('');
        const schedPayload = buildSchedulePayload();
        const payload = {
            name: form.name.trim(),
            marketplace_id: form.marketplace_id,
            api_token: form.api_token,
            region: form.region,
            ...buildSettingsPayload(),
            ...(schedPayload ? { sync_schedule: schedPayload } : {}),
        };
        createStore(payload)
            .then(() => { onSuccess(); onClose(); })
            .catch((err) => {
                const d = err.response?.data;
                let msg = d?.detail;
                if (!msg && typeof d === 'object') {
                    const parts = Object.entries(d).flatMap(([k, v]) =>
                        (Array.isArray(v) ? v : [v]).map((x) => (typeof x === 'string' ? x : `${k}: ${JSON.stringify(x)}`))
                    );
                    msg = parts.length ? parts.join('. ') : null;
                }
                setError(msg || 'Failed to create store');
            })
            .finally(() => setLoading(false));
    };

    if (!open) return null;

    const showSteps = !isDuplicate;
    const maxStep = showSteps ? 3 : 1;

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="fixed inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />
            <div
                className={`relative rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 shadow-xl overflow-hidden flex flex-col ${showSteps ? 'w-full max-w-6xl max-h-[90vh]' : 'w-full max-w-lg max-h-[90vh]'}`}
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex-shrink-0 border-b border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-6 pt-5 pb-0">
                    <div className="flex items-start justify-between gap-4 mb-4">
                        <div>
                            <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
                                {isDuplicate ? 'Duplicate Store' : 'Create New Store'}
                            </h2>
                            {isDuplicate && (
                                <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                                    Enter new store details. Price margins and inventory settings will be copied from <strong className="text-slate-700 dark:text-slate-300">{copyFromStore?.name}</strong>.
                                </p>
                            )}
                        </div>
                        <button type="button" className="p-2 rounded-md hover:bg-slate-100 dark:hover:bg-slate-800 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200" onClick={onClose}>
                            <X className="h-5 w-5" />
                        </button>
                    </div>
                    {!isDuplicate && (
                        <div className="flex">
                            {[
                                { num: 1, label: 'Store' },
                                { num: 2, label: 'Price' },
                                { num: 3, label: 'Inventory' },
                            ].map((t) => (
                                <button
                                    key={t.num}
                                    type="button"
                                    onClick={() => { setError(''); setStep(t.num); }}
                                    className={`relative flex items-center gap-2 px-5 py-2.5 text-sm font-medium transition-colors
                                        ${step === t.num
                                            ? 'text-accent-600 dark:text-accent-400'
                                            : 'text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200'
                                        }`}
                                >
                                    <span className={`flex items-center justify-center h-6 w-6 rounded-full text-xs font-semibold
                                        ${step === t.num
                                            ? 'bg-accent-600 text-white dark:bg-accent-500'
                                            : 'bg-slate-200 text-slate-600 dark:bg-slate-700 dark:text-slate-300'
                                        }`}>
                                        {t.num}
                                    </span>
                                    {t.label}
                                    {step === t.num && (
                                        <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-accent-600 dark:bg-accent-400 rounded-t" />
                                    )}
                                </button>
                            ))}
                        </div>
                    )}
                    {isDuplicate && <div className="pb-4" />}
                </div>

                <div className="flex-1 min-h-0 overflow-y-auto flex flex-col items-stretch" style={{ scrollbarWidth: 'thin', scrollbarColor: 'rgba(100,116,139,.35) transparent' }}>
                    <div className="px-6 py-4">
                        {error && (
                            <div className="rounded-lg border border-rose-200 dark:border-rose-800 bg-rose-50 dark:bg-rose-900/20 px-4 py-3 text-sm text-rose-700 dark:text-rose-300 mb-5">
                                {error}
                            </div>
                        )}

                        {/* Step 1: Store basics */}
                        {(step === 1 || isDuplicate) && (
                            <div className="space-y-5">
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                                    <div className="sm:col-span-2">
                                        <Input label="Store Name" placeholder="e.g. My Reverb Store" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} required />
                                    </div>
                                    <div className="sm:col-span-2">
                                        <Select
                                            label="Marketplace"
                                            value={form.marketplace_id}
                                            onChange={(e) => setForm((f) => ({ ...f, marketplace_id: e.target.value }))}
                                            options={[
                                                { value: '', label: 'Select marketplace' },
                                                ...([...marketplaces].sort((a, b) => (a.name || '').localeCompare(b.name || '')).map((m) => ({ value: m.id, label: m.name }))),
                                            ]}
                                            required
                                        />
                                    </div>
                                    <div className="sm:col-span-2">
                                        <Input label="API Key" type="password" placeholder="Enter marketplace API key" value={form.api_token} onChange={(e) => setForm((f) => ({ ...f, api_token: e.target.value }))} required />
                                    </div>
                                    <Select
                                        label="Region"
                                        value={form.region}
                                        onChange={(e) => {
                                            const newRegion = e.target.value;
                                            setForm((f) => ({
                                                ...f,
                                                region: newRegion,
                                                schedule_timezone: DEFAULT_TZ[newRegion] || f.schedule_timezone,
                                            }));
                                        }}
                                        options={[{ value: 'USA', label: 'USA' }, { value: 'AU', label: 'Australia' }]}
                                    />
                                </div>
                                <p className="text-xs text-slate-500 dark:text-slate-400">API key will be encrypted and stored securely.</p>

                                {/* Schedule section */}
                                <div className="mt-6 rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/50 p-5 space-y-4">
                                    <div className="flex items-center gap-3">
                                        <Clock className="h-5 w-5 text-slate-500 dark:text-slate-400" />
                                        <div className="flex-1">
                                            <h3 className="text-sm font-medium text-slate-900 dark:text-slate-100">Scheduled Updates</h3>
                                            <p className="text-xs text-slate-500 dark:text-slate-400">Automatically update price and inventory on the marketplace</p>
                                        </div>
                                        <label className="relative inline-flex items-center cursor-pointer">
                                            <input
                                                type="checkbox"
                                                checked={form.schedule_enabled}
                                                onChange={(e) => setForm((f) => ({ ...f, schedule_enabled: e.target.checked }))}
                                                className="sr-only peer"
                                            />
                                            <div className="w-9 h-5 bg-slate-300 dark:bg-slate-600 peer-focus:ring-2 peer-focus:ring-accent-400 rounded-full peer peer-checked:after:translate-x-full rtl:peer-checked:after:-translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:start-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-accent-500"></div>
                                        </label>
                                    </div>

                                    {form.schedule_enabled && (
                                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-2">
                                            <Select
                                                label="Frequency"
                                                value={form.schedule_frequency}
                                                onChange={(e) => setForm((f) => ({ ...f, schedule_frequency: e.target.value }))}
                                                options={FREQUENCY_OPTIONS}
                                            />
                                            <Select
                                                label="Timezone"
                                                value={form.schedule_timezone}
                                                onChange={(e) => setForm((f) => ({ ...f, schedule_timezone: e.target.value }))}
                                                options={regionTimezones}
                                            />
                                            {form.schedule_frequency === 'daily' && (
                                                <div className="sm:col-span-2 flex gap-3 items-end">
                                                    <div className="flex-1">
                                                        <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">Update time</label>
                                                        <div className="flex gap-2">
                                                            <select
                                                                value={form.schedule_hour}
                                                                onChange={(e) => setForm((f) => ({ ...f, schedule_hour: e.target.value }))}
                                                                className="rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 focus:border-accent-500 focus:ring-1 focus:ring-accent-500 outline-none"
                                                            >
                                                                {Array.from({ length: 24 }, (_, i) => (
                                                                    <option key={i} value={String(i)}>
                                                                        {i === 0 ? '12 AM' : i < 12 ? `${i} AM` : i === 12 ? '12 PM' : `${i - 12} PM`}
                                                                    </option>
                                                                ))}
                                                            </select>
                                                            <span className="text-slate-500 dark:text-slate-400 self-center">:</span>
                                                            <select
                                                                value={form.schedule_minute}
                                                                onChange={(e) => setForm((f) => ({ ...f, schedule_minute: e.target.value }))}
                                                                className="rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 focus:border-accent-500 focus:ring-1 focus:ring-accent-500 outline-none"
                                                            >
                                                                {['00', '15', '30', '45'].map((m) => (
                                                                    <option key={m} value={m}>{m}</option>
                                                                ))}
                                                            </select>
                                                        </div>
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    )}
                                </div>
                            </div>
                        )}

                        {/* Step 2: Price (create only) */}
                        {showSteps && step === 2 && (
                            <div className="space-y-3 w-full">
                                <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50/80 dark:bg-slate-800/40 p-4 space-y-3">
                                    <p className="text-sm text-slate-600 dark:text-slate-300">Add at least one vendor with price rules.</p>
                                    <div className="flex flex-col sm:flex-row gap-3 sm:items-end">
                                        <div className="flex-1 min-w-0">
                                            <Select
                                                label="Vendor to add"
                                                placeholder="Select vendor to add"
                                                value={selectedVendorPrice}
                                                onChange={(e) => { const vid = e.target.value; if (vid) addVendorPrice(vid); }}
                                                options={[
                                                    { value: '', label: 'Select vendor to add' },
                                                    ...(vendors.filter((v) => !usedPriceVendorIds.includes(v.id)).length > 0 ? [{ value: '__all__', label: 'All vendors' }] : []),
                                                    ...vendors.filter((v) => !usedPriceVendorIds.includes(v.id)).map((v) => ({ value: v.id, label: v.name })),
                                                ]}
                                            />
                                        </div>
                                        <Button type="button" variant="secondary" size="sm" className="shrink-0 sm:mb-0.5" onClick={() => addVendorPrice()}>
                                            <Plus className="h-4 w-4 mr-1.5 inline" aria-hidden /> Add vendor
                                        </Button>
                                    </div>
                                </div>
                                {(form.vendor_price_settings || []).map((vp, i) => (
                                    <div key={i} className="rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/50 p-4 space-y-3">
                                        <div className="flex flex-col sm:flex-row sm:items-end gap-3 pb-3 border-b border-slate-200 dark:border-slate-600">
                                            <div className="flex-1 min-w-0">
                                                <Select
                                                    label="Vendor"
                                                    value={vp.vendor_id || ''}
                                                    onChange={(e) => updateVendorPrice(i, 'vendor_id', e.target.value)}
                                                    options={[{ value: '', label: 'Select vendor' }, ...vendors.map((v) => ({ value: String(v.id), label: v.name }))]}
                                                    className="w-full max-w-md"
                                                />
                                            </div>
                                            <Button type="button" variant="danger" size="sm" className="shrink-0" onClick={() => removeVendorPrice(i)}>
                                                <Trash2 className="h-4 w-4 mr-1.5 inline" aria-hidden /> Delete vendor
                                            </Button>
                                        </div>
                                        {(() => {
                                            const allDirect = (vp.range_margins || []).length > 0 && (vp.range_margins || []).every((r) => r.margin_type === 'direct');
                                            return (
                                                <>
                                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                                    <Input label="Purchase Tax (%)" type="number" step="0.01" min={0} value={allDirect ? 0 : vp.purchase_tax_percentage} disabled={allDirect} onChange={(e) => { const v = e.target.value; if (v === '') { updateVendorPrice(i, 'purchase_tax_percentage', ''); return; } const n = parseFloat(v); updateVendorPrice(i, 'purchase_tax_percentage', Number.isFinite(n) ? Math.max(0, n) : ''); }} />
                                                    <Input label="Marketplace Fees (%)" type="number" step="0.01" min={0} value={allDirect ? 0 : vp.marketplace_fees_percentage} disabled={allDirect} onChange={(e) => { const v = e.target.value; if (v === '') { updateVendorPrice(i, 'marketplace_fees_percentage', ''); return; } const n = parseFloat(v); updateVendorPrice(i, 'marketplace_fees_percentage', Number.isFinite(n) ? Math.max(0, n) : ''); }} />
                                                </div>
                                                <label className="flex items-center gap-2.5 cursor-pointer select-none">
                                                    <input
                                                        type="checkbox"
                                                        checked={(vp.rounding_option || 'none') === 'nearest_99'}
                                                        onChange={(e) => updateVendorPrice(i, 'rounding_option', e.target.checked ? 'nearest_99' : 'none')}
                                                        className="h-4 w-4 rounded border-slate-300 dark:border-slate-600 text-accent-600 focus:ring-accent-500"
                                                    />
                                                    <span className="text-sm text-slate-700 dark:text-slate-300">Round final price to .99</span>
                                                    <span className="text-xs text-slate-400 dark:text-slate-500">(e.g. $56.66 → $56.99)</span>
                                                </label>
                                                </>
                                            );
                                        })()}
                                        <div>
                                            <div className="text-sm font-medium text-slate-700 dark:text-slate-300">Price ranges</div>
                                            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                                                Tiers must be continuous. Last tier &quot;To&quot; must be 999999999. Use Percentage or Fixed margin per tier.
                                            </p>
                                        </div>
                                        <div className="space-y-3">
                                            {(vp.range_margins || []).map((r, ri) => (
                                                <div key={ri} className="rounded-lg border border-slate-200 dark:border-slate-600 bg-white/50 dark:bg-slate-900/40 p-3 space-y-3">
                                                    <div className="grid grid-cols-2 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                                                        <Input label="From" type="number" min={0} step="1" value={r.from_value} onChange={(e) => { const v = e.target.value; if (v === '') { updatePriceRange(i, ri, 'from_value', ''); return; } updatePriceRange(i, ri, 'from_value', Math.max(0, parseFloat(v) || 0)); }} />
                                                        <Input label="To" placeholder="999999999" type="number" min={0} step="1" value={r.to_value ?? ''} onChange={(e) => { const v = e.target.value; if (v === '') { updatePriceRange(i, ri, 'to_value', ''); return; } updatePriceRange(i, ri, 'to_value', Math.max(0, parseFloat(v) || 0)); }} />
                                                        <Select
                                                            label="Margin type"
                                                            value={r.margin_type || 'percentage'}
                                                            onChange={(e) => updatePriceRange(i, ri, 'margin_type', e.target.value)}
                                                            options={[{ value: 'percentage', label: 'Percentage' }, { value: 'fixed', label: 'Fixed' }, { value: 'direct', label: 'Direct' }]}
                                                            className="min-w-0"
                                                        />
                                                        <Input
                                                            label={r.margin_type === 'fixed' ? 'Amount ($)' : r.margin_type === 'direct' ? 'Multiplier (×)' : 'Margin (%)'}
                                                            type="number"
                                                            min={0}
                                                            step="0.01"
                                                            value={r.margin_percentage}
                                                            onChange={(e) => { const v = e.target.value; if (v === '') { updatePriceRange(i, ri, 'margin_percentage', ''); return; } const n = parseFloat(v); updatePriceRange(i, ri, 'margin_percentage', Number.isFinite(n) ? Math.max(0, n) : 0); }}
                                                        />
                                                    </div>
                                                    <div className="flex flex-wrap gap-2">
                                                        <Button type="button" variant="secondary" size="sm" onClick={() => addPriceRange(i)}>
                                                            <Plus className="h-4 w-4 mr-1.5 inline" aria-hidden /> Add tier
                                                        </Button>
                                                        {(vp.range_margins?.length || 0) > 1 && (
                                                            <Button type="button" variant="danger" size="sm" onClick={() => removePriceRange(i, ri)}>
                                                                <Trash2 className="h-4 w-4 mr-1.5 inline" aria-hidden /> Delete tier
                                                            </Button>
                                                        )}
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}

                        {/* Step 3: Inventory (create only) */}
                        {showSteps && step === 3 && (
                            <div className="space-y-3 w-full">
                                <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50/80 dark:bg-slate-800/40 p-4 space-y-3">
                                    <p className="text-sm text-slate-600 dark:text-slate-300">Add at least one vendor with inventory ranges. Default range: 0 – 999999999.</p>
                                    <div className="flex flex-col sm:flex-row gap-3 sm:items-end">
                                        <div className="flex-1 min-w-0">
                                            <Select
                                                label="Vendor to add"
                                                placeholder="Select vendor to add"
                                                value={selectedVendorInventory}
                                                onChange={(e) => { const vid = e.target.value; if (vid) addVendorInventory(vid); }}
                                                options={[
                                                    { value: '', label: 'Select vendor to add' },
                                                    ...(vendors.filter((v) => !usedInventoryVendorIds.includes(v.id)).length > 0 ? [{ value: '__all__', label: 'All vendors' }] : []),
                                                    ...vendors.filter((v) => !usedInventoryVendorIds.includes(v.id)).map((v) => ({ value: v.id, label: v.name })),
                                                ]}
                                            />
                                        </div>
                                        <Button type="button" variant="secondary" size="sm" className="shrink-0 sm:mb-0.5" onClick={() => addVendorInventory()}>
                                            <Plus className="h-4 w-4 mr-1.5 inline" aria-hidden /> Add vendor
                                        </Button>
                                    </div>
                                </div>
                                {(form.vendor_inventory_settings || []).map((vi, i) => (
                                    <div key={i} className="rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/50 p-4 space-y-3">
                                        <div className="flex flex-col sm:flex-row sm:items-end gap-3 pb-3 border-b border-slate-200 dark:border-slate-600">
                                            <div className="flex-1 min-w-0">
                                                <Select
                                                    label="Vendor"
                                                    value={vi.vendor_id || ''}
                                                    onChange={(e) => updateVendorInventory(i, 'vendor_id', e.target.value)}
                                                    options={[{ value: '', label: 'Select vendor' }, ...vendors.map((v) => ({ value: String(v.id), label: v.name }))]}
                                                    className="w-full max-w-md"
                                                />
                                            </div>
                                            <Button type="button" variant="danger" size="sm" className="shrink-0" onClick={() => removeVendorInventory(i)}>
                                                <Trash2 className="h-4 w-4 mr-1.5 inline" aria-hidden /> Delete vendor
                                            </Button>
                                        </div>
                                        <div className="text-sm font-medium text-slate-700 dark:text-slate-300">Inventory ranges</div>
                                        <div className="space-y-3">
                                            {(vi.range_multipliers || []).map((r, ri) => (
                                                <div key={ri} className="rounded-lg border border-slate-200 dark:border-slate-600 bg-white/50 dark:bg-slate-900/40 p-3 space-y-3">
                                                    <div className="grid grid-cols-2 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                                                        <Input label="From" type="number" min={0} value={r.from_value} onChange={(e) => updateInventoryRange(i, ri, 'from_value', Math.max(0, parseFloat(e.target.value) || 0))} />
                                                        <Input label="To" placeholder="999999999" type="number" min={0} value={r.to_value ?? ''} onChange={(e) => { const v = e.target.value; updateInventoryRange(i, ri, 'to_value', v === '' ? '' : Math.max(0, parseFloat(v) || 0)); }} />
                                                        <Select label="Type" value={r.range_type ?? 'multiplier'} onChange={(e) => updateInventoryRange(i, ri, 'range_type', e.target.value)} options={[{ value: 'multiplier', label: 'Multiplier' }, { value: 'fixed', label: 'Fixed' }]} className="min-w-0" />
                                                        {r.range_type === 'fixed' ? (
                                                            <Input label="Fixed" type="number" min={0} value={r.fixed_value ?? ''} onChange={(e) => updateInventoryRange(i, ri, 'fixed_value', Math.max(0, parseInt(e.target.value, 10) || 0))} />
                                                        ) : (
                                                            <Input label="Multiplier" type="number" step="0.01" min={0} value={r.multiplier} onChange={(e) => updateInventoryRange(i, ri, 'multiplier', Math.max(0, parseFloat(e.target.value) || 0))} />
                                                        )}
                                                    </div>
                                                    <div className="flex flex-wrap gap-2">
                                                        <Button type="button" variant="secondary" size="sm" onClick={() => addInventoryRange(i)}>
                                                            <Plus className="h-4 w-4 mr-1.5 inline" aria-hidden /> Add range
                                                        </Button>
                                                        {(vi.range_multipliers?.length || 0) > 1 && (
                                                            <Button type="button" variant="danger" size="sm" onClick={() => removeInventoryRange(i, ri)}>
                                                                <Trash2 className="h-4 w-4 mr-1.5 inline" aria-hidden /> Delete range
                                                            </Button>
                                                        )}
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                </div>

                <div className="flex-shrink-0 flex justify-between items-center border-t border-slate-200 dark:border-slate-700 px-6 py-4">
                    <Button
                        variant="ghost"
                        onClick={() => setStep((s) => s - 1)}
                        disabled={step === 1 || isDuplicate}
                    >
                        Back
                    </Button>
                    <Button
                        variant="primary"
                        disabled={loading || (step === 2 && !(form.vendor_price_settings || []).some((vp) => vp.vendor_id)) || (step === 3 && !(form.vendor_inventory_settings || []).some((vi) => vi.vendor_id))}
                        onClick={handleSubmit}
                    >
                        {loading ? 'Creating…' : isDuplicate ? 'Duplicate Store' : step < 3 ? 'Continue' : 'Create Store'}
                    </Button>
                </div>
            </div>
        </div>
    );
}
