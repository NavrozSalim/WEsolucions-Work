import { AlertTriangle, Clock, Plus, RefreshCw, Trash2 } from 'lucide-react';
import Select from '../ui/Select';

/**
 * Auto Scheduling step (store wizards): the Auto Scraping and Scheduled Updates cards.
 *
 * Both cards work the same way: run times in a timezone, then "retry failed cycles"
 * (Failed listings back to Pending and scraped again). Scheduled Updates then sends
 * price/stock to the marketplace once; Auto Scraping never sends anything.
 * Inventory-only stores can give a vendor its own times ("Vendor times"); that vendor
 * is then left out of the store-wide times of the same card.
 *
 * Saved as the store's ``auto_scrape`` and ``sync_schedule`` objects (each with ``vendors``).
 */

export const SCRAPE_MAX_TIMES = 6;
export const UPDATE_MAX_TIMES = 12;
export const MAX_RETRY_CYCLES = 10;

const KIND = {
    scrape: {
        prefix: 'auto_scrape',
        title: 'Auto Scraping',
        subtitle: 'Scrape vendors at set times and retry failed products. Nothing is sent to the marketplace.',
        icon: RefreshCw,
        maxTimes: SCRAPE_MAX_TIMES,
        defaultTime: '06:00',
        help: 'At each run time the listings are set to Pending and scraped. Then, up to this many times, '
            + 'Failed listings are set back to Pending and scraped again (it stops early when nothing is Failed). '
            + 'Auto Scraping only scrapes: nothing is sent to the marketplace, not even stock 0 for Failed listings.',
    },
    update: {
        prefix: 'schedule',
        title: 'Scheduled Updates',
        subtitle: 'Scrape, retry failed products, then send price/inventory to the marketplace.',
        icon: Clock,
        maxTimes: UPDATE_MAX_TIMES,
        defaultTime: '10:00',
        help: 'At each run time the listings are set to Pending and scraped, Failed listings are retried up to '
            + 'this many times, then price and stock are sent to the marketplace once (Failed listings with stock 0). '
            + 'MyDeal stores: the run queues "Upload the file on the Marketplace" for the inventory bot laptop.',
    },
};

const SELECT_CLASS = 'rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 focus:border-accent-500 focus:ring-1 focus:ring-accent-500 outline-hidden';
const INPUT_CLASS = 'w-full rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 focus:border-accent-500 focus:ring-1 focus:ring-accent-500 outline-hidden';

const HOURS = Array.from({ length: 24 }, (_, i) => ({
    value: String(i).padStart(2, '0'),
    label: i === 0 ? '12 AM' : i < 12 ? `${i} AM` : i === 12 ? '12 PM' : `${i - 12} PM`,
}));
const MINUTES = Array.from({ length: 12 }, (_, i) => String(i * 5).padStart(2, '0'));

const splitTime = (t, fallback = '06:00') => {
    const [h = '06', m = '00'] = String(t || fallback).split(':');
    return [h.padStart(2, '0'), m.padStart(2, '0')];
};

const clampCycles = (raw) => (raw === '' ? '' : Math.max(0, Math.min(MAX_RETRY_CYCLES, Math.trunc(Number(raw)) || 0)));

let rowKeySeq = 0;
const nextRowKey = () => {
    rowKeySeq += 1;
    return `vs-${Date.now()}-${rowKeySeq}`;
};

function vendorRowsFromStore(rows) {
    return (rows || []).map((r) => ({
        key: nextRowKey(),
        vendor_id: String(r.vendor_id || ''),
        times: r.run_times?.length ? [...r.run_times] : [],
        retry_cycles: r.retry_failed_cycles ?? 2,
    }));
}

/** Form fields for a new store. Scheduled Updates is on (it is required when creating). */
export function emptyScheduleFields(timezone = 'America/New_York') {
    return {
        auto_scrape_enabled: false,
        auto_scrape_times: [KIND.scrape.defaultTime],
        auto_scrape_timezone: timezone,
        auto_scrape_retry_cycles: 2,
        auto_scrape_vendors: [],
        schedule_enabled: true,
        schedule_times: [KIND.update.defaultTime],
        schedule_timezone: timezone,
        schedule_retry_cycles: 2,
        schedule_vendors: [],
    };
}

/** Form fields from a saved store (``auto_scrape``, ``sync_schedule``, ``vendor_schedules``). */
export function scheduleFieldsFromStore(store, fallbackTimezone) {
    const a = store?.auto_scrape;
    const s = store?.sync_schedule;
    const vs = store?.vendor_schedules || {};
    return {
        auto_scrape_enabled: !!a?.enabled,
        auto_scrape_times: a?.run_times?.length ? [...a.run_times] : [KIND.scrape.defaultTime],
        auto_scrape_timezone: a?.timezone || fallbackTimezone,
        auto_scrape_retry_cycles: a?.retry_failed_cycles ?? 2,
        auto_scrape_vendors: vendorRowsFromStore(vs.scrape),
        schedule_enabled: !!(s?.enabled ?? s?.is_active),
        schedule_times: s?.run_times?.length ? [...s.run_times] : [KIND.update.defaultTime],
        schedule_timezone: s?.timezone || fallbackTimezone,
        schedule_retry_cycles: s ? (s.retry_failed_cycles ?? 0) : 2,
        schedule_vendors: vendorRowsFromStore(vs.update),
    };
}

function validateCard(form, kind, allowVendors) {
    const cfg = KIND[kind];
    const p = cfg.prefix;
    const errs = [];
    const times = form[`${p}_times`] || [];
    if (!times.length) errs.push(`${cfg.title}: add at least one run time.`);
    if (new Set(times).size !== times.length) errs.push(`${cfg.title}: each run time must be different.`);
    if (times.length > cfg.maxTimes) errs.push(`${cfg.title}: at most ${cfg.maxTimes} run times a day.`);
    const n = Number(form[`${p}_retry_cycles`]);
    if (form[`${p}_retry_cycles`] === '' || !Number.isInteger(n) || n < 0 || n > MAX_RETRY_CYCLES) {
        errs.push(`${cfg.title}: retry failed cycles must be 0 to ${MAX_RETRY_CYCLES}.`);
    }
    if (allowVendors) {
        const rows = form[`${p}_vendors`] || [];
        const seen = new Set();
        rows.forEach((row) => {
            if (!row.vendor_id) {
                errs.push(`${cfg.title}: choose a vendor for each vendor time.`);
                return;
            }
            if (seen.has(row.vendor_id)) errs.push(`${cfg.title}: each vendor can only be added once.`);
            seen.add(row.vendor_id);
            const vt = row.times || [];
            if (!vt.length) errs.push(`${cfg.title}: add at least one run time for each vendor.`);
            if (new Set(vt).size !== vt.length) errs.push(`${cfg.title}: a vendor's run times must be different.`);
            if (vt.length > cfg.maxTimes) errs.push(`${cfg.title}: at most ${cfg.maxTimes} run times a day per vendor.`);
            const vn = Number(row.retry_cycles);
            if (row.retry_cycles === '' || !Number.isInteger(vn) || vn < 0 || vn > MAX_RETRY_CYCLES) {
                errs.push(`${cfg.title}: a vendor's retry failed cycles must be 0 to ${MAX_RETRY_CYCLES}.`);
            }
        });
    }
    return [...new Set(errs)];
}

/** Errors for the Auto Scheduling step (empty when OK). */
export function validateScheduleFields(form, { requireUpdates = false, allowVendors = true } = {}) {
    const errs = [];
    if (requireUpdates && !form.schedule_enabled) errs.push('Scheduled Updates is required.');
    if (form.auto_scrape_enabled) errs.push(...validateCard(form, 'scrape', allowVendors));
    if (form.schedule_enabled) errs.push(...validateCard(form, 'update', allowVendors));
    return errs;
}

function cardPayload(form, kind, allowVendors) {
    const p = KIND[kind].prefix;
    if (!form[`${p}_enabled`]) return { enabled: false };
    const payload = {
        enabled: true,
        run_times: [...new Set(form[`${p}_times`] || [])].sort(),
        timezone: form[`${p}_timezone`],
        retry_failed_cycles: Number(form[`${p}_retry_cycles`]) || 0,
        vendors: allowVendors
            ? (form[`${p}_vendors`] || []).filter((r) => r.vendor_id).map((r) => ({
                vendor_id: r.vendor_id,
                run_times: [...new Set(r.times || [])].sort(),
                retry_failed_cycles: Number(r.retry_cycles) || 0,
            }))
            : [],
    };
    if (kind === 'scrape') payload.push_after = false; // Auto Scraping never pushes.
    return payload;
}

/** ``{ auto_scrape, sync_schedule }`` for the store create/update request. */
export function buildSchedulePayloads(form, { allowVendors = true } = {}) {
    return {
        auto_scrape: cardPayload(form, 'scrape', allowVendors),
        sync_schedule: cardPayload(form, 'update', allowVendors),
    };
}

const STATUS_TEXT = {
    waiting: 'waiting for the store to be free',
    running: 'running now',
    success: 'finished, nothing failed',
    partial: 'finished',
    stopped: 'stopped',
    failed: 'failed',
    skipped: 'skipped',
};

const PUSH_TEXT = {
    done: 'sent to marketplace',
    cancelled: 'marketplace push cancelled',
    failed: 'marketplace push failed',
    skipped_not_connected: 'not sent (store not connected)',
    skipped_push_already_running: 'not sent (a Manual sync was running)',
    skipped_scrape_still_running: 'not sent (scrape still running)',
    queued: 'upload queued for the bot laptop',
    skipped_upload_already_queued: 'upload not queued (another upload was waiting or running)',
    skipped_no_template_rows: 'upload not queued (no MyDeal template rows)',
    skipped_upload_error: 'upload not queued (see Logs)',
};

const SKIP_TEXT = {
    waited_too_long: 'the store was busy for 3 hours',
    turned_off: 'it was turned off',
    scrape_running: 'a scrape was already running',
    worker_lost: 'the server restarted',
    nothing_in_scope: 'no listings',
};

export function formatLastRun(info, kind) {
    if (!info?.last_run_at && info?.last_status !== 'waiting') return '';
    const s = info.last_summary || {};
    const when = info.last_run_at ? new Date(info.last_run_at).toLocaleString() : '';
    let status = STATUS_TEXT[info.last_status] || info.last_status || '';
    const reason = SKIP_TEXT[s.stop_reason];
    if ((info.last_status === 'skipped' || info.last_status === 'failed') && reason) status = `${status} (${reason})`;
    if (s.stop_reason === 'nothing_in_scope') status = 'nothing to scrape (no listings left for these times)';
    const trail = Array.isArray(s.failed_after) && s.failed_after.length ? s.failed_after.join(' → ') : null;
    const whenLabel = info.last_status === 'waiting' ? 'Queued' : info.last_status === 'running' ? 'Started' : 'Last run';
    const parts = [
        when ? `${whenLabel} ${when}` : 'Queued',
        status,
        s.cycles_run != null && ['success', 'partial', 'stopped'].includes(info.last_status) ? `${s.cycles_run} retry cycle(s)` : null,
        trail ? `failed: ${trail}` : null,
    ];
    if (kind === 'update' && s.push) {
        const pushed = s.push === 'done' ? `${s.pushed ?? 0} sent to marketplace` : (PUSH_TEXT[s.push] || s.push);
        parts.push(pushed);
    }
    return parts.filter(Boolean).join(' · ');
}

function RunTimesEditor({ times, onChange, maxTimes, defaultTime, label = 'Run times', idPrefix }) {
    const setTime = (idx, part, value) => {
        onChange(times.map((t, i) => {
            if (i !== idx) return t;
            const [h, m] = splitTime(t, defaultTime);
            return part === 'h' ? `${value}:${m}` : `${h}:${value}`;
        }));
    };
    const addTime = () => {
        if (times.length >= maxTimes) return;
        const used = new Set(times);
        const step = maxTimes > 6 ? 2 : 6;
        let h = times.length ? (Number(splitTime(times[times.length - 1], defaultTime)[0]) + step) % 24 : Number(defaultTime.slice(0, 2));
        let candidate = `${String(h).padStart(2, '0')}:00`;
        for (let i = 0; i < 24 && used.has(candidate); i += 1) {
            h = (h + 1) % 24;
            candidate = `${String(h).padStart(2, '0')}:00`;
        }
        onChange([...times, candidate]);
    };
    return (
        <div>
            {label && <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">{label}</label>}
            <div className="flex flex-wrap gap-x-4 gap-y-2">
                {times.map((t, idx) => {
                    const [h, m] = splitTime(t, defaultTime);
                    const minuteOptions = MINUTES.includes(m) ? MINUTES : [...MINUTES, m].sort();
                    return (
                        <div key={idx} className="flex gap-1.5 items-center">
                            <select value={h} onChange={(e) => setTime(idx, 'h', e.target.value)} className={SELECT_CLASS} aria-label={`${idPrefix} run time ${idx + 1} hour`}>
                                {HOURS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                            </select>
                            <span className="text-slate-500 dark:text-slate-400">:</span>
                            <select value={m} onChange={(e) => setTime(idx, 'm', e.target.value)} className={SELECT_CLASS} aria-label={`${idPrefix} run time ${idx + 1} minute`}>
                                {minuteOptions.map((o) => <option key={o} value={o}>{o}</option>)}
                            </select>
                            {times.length > 1 && (
                                <button
                                    type="button"
                                    onClick={() => onChange(times.filter((_, i) => i !== idx))}
                                    className="p-1.5 text-slate-400 hover:text-red-500"
                                    aria-label={`Remove ${idPrefix} run time ${idx + 1}`}
                                >
                                    <Trash2 className="h-4 w-4" />
                                </button>
                            )}
                        </div>
                    );
                })}
            </div>
            {times.length < maxTimes && (
                <button
                    type="button"
                    onClick={addTime}
                    className="mt-2 inline-flex items-center gap-1 text-sm text-accent-600 hover:text-accent-700 dark:text-accent-400"
                >
                    <Plus className="h-4 w-4" /> Add time
                </button>
            )}
        </div>
    );
}

function CyclesInput({ value, onChange, id }) {
    return (
        <input
            id={id}
            type="number"
            min={0}
            max={MAX_RETRY_CYCLES}
            step={1}
            value={value}
            onChange={(e) => onChange(clampCycles(e.target.value))}
            className={INPUT_CLASS}
        />
    );
}

export default function ScheduleCard({
    kind,
    form,
    setForm,
    timezoneOptions,
    vendorOptions = [],
    allowVendors = true,
    required = false,
    storeActive = true,
    lastRun = null,
    vendorRuns = [],
    className = '',
}) {
    const cfg = KIND[kind];
    const p = cfg.prefix;
    const Icon = cfg.icon;
    const enabled = required || !!form[`${p}_enabled`];
    const times = form[`${p}_times`] || [];
    const rows = form[`${p}_vendors`] || [];
    const set = (patch) => setForm((f) => ({ ...f, ...patch }));
    const setRows = (updater) => setForm((f) => ({ ...f, [`${p}_vendors`]: updater(f[`${p}_vendors`] || []) }));
    const usedVendorIds = new Set(rows.map((r) => r.vendor_id).filter(Boolean));
    const freeVendors = vendorOptions.filter((v) => !usedVendorIds.has(v.value));
    const vendorLabel = (id) => vendorOptions.find((v) => v.value === id)?.label || 'Vendor';
    const runByVendor = Object.fromEntries((vendorRuns || []).map((r) => [String(r.vendor_id), r]));
    const lastRunText = formatLastRun(lastRun, kind);

    const addVendorRow = () => {
        if (!freeVendors.length) return;
        setRows((list) => [...list, {
            key: nextRowKey(),
            vendor_id: freeVendors[0].value,
            times: [cfg.defaultTime],
            retry_cycles: form[`${p}_retry_cycles`] === '' ? 2 : Number(form[`${p}_retry_cycles`]) || 0,
        }]);
    };
    const updateRow = (key, patch) => setRows((list) => list.map((r) => (r.key === key ? { ...r, ...patch } : r)));
    const removeRow = (key) => setRows((list) => list.filter((r) => r.key !== key));

    return (
        <div className={`rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/50 p-5 space-y-4 ${className}`}>
            <div className="flex items-center gap-3">
                <Icon className="h-5 w-5 text-slate-500 dark:text-slate-400" />
                <div className="flex-1">
                    <h3 className="text-sm font-medium text-slate-900 dark:text-slate-100">
                        {cfg.title}
                        {required && <span className="ml-2 text-xs font-normal text-slate-500 dark:text-slate-400">Required</span>}
                    </h3>
                    <p className="text-xs text-slate-500 dark:text-slate-400">{cfg.subtitle}</p>
                </div>
                {!required && (
                    <label className="relative inline-flex items-center cursor-pointer">
                        <input
                            type="checkbox"
                            checked={enabled}
                            onChange={(e) => set({ [`${p}_enabled`]: e.target.checked })}
                            className="sr-only peer"
                            aria-label={cfg.title}
                        />
                        <div className="w-9 h-5 bg-slate-300 dark:bg-slate-600 peer-focus:ring-2 peer-focus:ring-accent-400 rounded-full peer peer-checked:after:translate-x-full peer-checked:rtl:after:-translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:inset-s-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-accent-500"></div>
                    </label>
                )}
            </div>

            {enabled && !storeActive && (
                <div role="alert" className="flex items-start gap-2 rounded-md border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
                    <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
                    <span>
                        This store is inactive, so {cfg.title} won&apos;t run until you turn the store on
                        (Store Settings → Active).
                    </span>
                </div>
            )}

            {enabled && (
                <div className="space-y-4 pt-1">
                    <RunTimesEditor
                        times={times}
                        onChange={(next) => set({ [`${p}_times`]: next })}
                        maxTimes={cfg.maxTimes}
                        defaultTime={cfg.defaultTime}
                        label={rows.length ? 'Run times (all other vendors)' : 'Run times'}
                        idPrefix={cfg.title}
                    />
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                        <Select
                            label="Timezone"
                            value={form[`${p}_timezone`]}
                            onChange={(e) => set({ [`${p}_timezone`]: e.target.value })}
                            options={timezoneOptions}
                        />
                        <div>
                            <label htmlFor={`${p}-cycles`} className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">Retry failed cycles</label>
                            <CyclesInput
                                id={`${p}-cycles`}
                                value={form[`${p}_retry_cycles`]}
                                onChange={(v) => set({ [`${p}_retry_cycles`]: v })}
                            />
                        </div>
                    </div>
                    <p className="text-xs text-slate-500 dark:text-slate-400">{cfg.help}</p>

                    <div className="border-t border-slate-200 dark:border-slate-700 pt-4">
                        <div className="flex items-center justify-between gap-3">
                            <div>
                                <h4 className="text-sm font-medium text-slate-800 dark:text-slate-200">Vendor times</h4>
                                <p className="text-xs text-slate-500 dark:text-slate-400">
                                    {allowVendors
                                        ? 'Give a vendor its own times. That vendor is then left out of the run times above.'
                                        : 'Vendor times are only for inventory-only stores.'}
                                </p>
                            </div>
                            {allowVendors && (
                                <button
                                    type="button"
                                    onClick={addVendorRow}
                                    disabled={!freeVendors.length}
                                    title={!vendorOptions.length ? 'Add vendors on the Price step first' : undefined}
                                    className="inline-flex shrink-0 items-center gap-1 text-sm text-accent-600 hover:text-accent-700 dark:text-accent-400 disabled:opacity-40 disabled:cursor-not-allowed"
                                >
                                    <Plus className="h-4 w-4" /> Add vendor
                                </button>
                            )}
                        </div>

                        {allowVendors && rows.length > 0 && (
                            <div className="mt-3 space-y-3">
                                {rows.map((row) => {
                                    const options = [
                                        ...(row.vendor_id && !vendorOptions.some((v) => v.value === row.vendor_id)
                                            ? [{ value: row.vendor_id, label: 'Vendor not on the Price step' }]
                                            : []),
                                        ...vendorOptions.filter((v) => v.value === row.vendor_id || !usedVendorIds.has(v.value)),
                                    ];
                                    const runText = formatLastRun(runByVendor[row.vendor_id], kind);
                                    return (
                                        <div key={row.key} className="rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900/40 p-3 space-y-3">
                                            <div className="flex items-end gap-3">
                                                <div className="flex-1">
                                                    <Select
                                                        label="Vendor"
                                                        value={row.vendor_id}
                                                        onChange={(e) => updateRow(row.key, { vendor_id: e.target.value })}
                                                        options={options}
                                                    />
                                                </div>
                                                <div className="w-40">
                                                    <label htmlFor={`${row.key}-cycles`} className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">Retry failed cycles</label>
                                                    <CyclesInput
                                                        id={`${row.key}-cycles`}
                                                        value={row.retry_cycles}
                                                        onChange={(v) => updateRow(row.key, { retry_cycles: v })}
                                                    />
                                                </div>
                                                <button
                                                    type="button"
                                                    onClick={() => removeRow(row.key)}
                                                    className="p-2 mb-0.5 text-slate-400 hover:text-red-500"
                                                    aria-label={`Remove vendor times for ${vendorLabel(row.vendor_id)}`}
                                                >
                                                    <Trash2 className="h-4 w-4" />
                                                </button>
                                            </div>
                                            <RunTimesEditor
                                                times={row.times || []}
                                                onChange={(next) => updateRow(row.key, { times: next })}
                                                maxTimes={cfg.maxTimes}
                                                defaultTime={cfg.defaultTime}
                                                idPrefix={`${vendorLabel(row.vendor_id)} ${cfg.title}`}
                                            />
                                            {runText && <p className="text-xs text-slate-500 dark:text-slate-400">{runText}</p>}
                                        </div>
                                    );
                                })}
                            </div>
                        )}
                    </div>
                </div>
            )}

            {lastRunText && <p className="text-xs text-slate-500 dark:text-slate-400">{lastRunText}</p>}
        </div>
    );
}

/** The whole "Auto Scheduling" wizard step: both cards. */
export function AutoSchedulingStep({
    form,
    setForm,
    timezoneOptions,
    vendorOptions,
    allowVendors,
    requireUpdates = false,
    storeActive = true,
    store = null,
}) {
    const vs = store?.vendor_schedules || {};
    return (
        <div className="space-y-4">
            <p className="text-sm text-slate-600 dark:text-slate-400">
                Set when this store is scraped and updated. Runs on one store happen one at a time: a run that comes
                due while another is going waits for it (up to 3 hours).
            </p>
            <ScheduleCard
                kind="scrape"
                form={form}
                setForm={setForm}
                timezoneOptions={timezoneOptions}
                vendorOptions={vendorOptions}
                allowVendors={allowVendors}
                storeActive={storeActive}
                lastRun={store?.auto_scrape}
                vendorRuns={vs.scrape}
            />
            <ScheduleCard
                kind="update"
                form={form}
                setForm={setForm}
                timezoneOptions={timezoneOptions}
                vendorOptions={vendorOptions}
                allowVendors={allowVendors}
                required={requireUpdates}
                storeActive={storeActive}
                lastRun={store?.sync_schedule}
                vendorRuns={vs.update}
            />
        </div>
    );
}
