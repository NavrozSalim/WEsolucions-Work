import { AlertTriangle, Plus, RefreshCw, Trash2 } from 'lucide-react';
import Select from '../ui/Select';

/**
 * Auto Scraping (store wizard, above Scheduled Updates). Scrape only; no marketplace push.
 *
 * At each run time the server sets all active listings to Pending and scrapes,
 * then up to N times resets only Failed listings to Pending and scrapes again.
 * Saved as the store's ``auto_scrape`` object.
 */

export const AUTO_SCRAPE_MAX_TIMES = 6;
export const AUTO_SCRAPE_MAX_CYCLES = 10;

const SELECT_CLASS = 'rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 focus:border-accent-500 focus:ring-1 focus:ring-accent-500 outline-hidden';

const HOURS = Array.from({ length: 24 }, (_, i) => ({
    value: String(i).padStart(2, '0'),
    label: i === 0 ? '12 AM' : i < 12 ? `${i} AM` : i === 12 ? '12 PM' : `${i - 12} PM`,
}));
const MINUTES = Array.from({ length: 12 }, (_, i) => String(i * 5).padStart(2, '0'));

const splitTime = (t) => {
    const [h = '06', m = '00'] = String(t || '06:00').split(':');
    return [h.padStart(2, '0'), m.padStart(2, '0')];
};

export function emptyAutoScrapeFields(timezone = 'America/New_York') {
    return {
        auto_scrape_enabled: false,
        auto_scrape_times: ['06:00'],
        auto_scrape_timezone: timezone,
        auto_scrape_retry_cycles: 2,
    };
}

export function autoScrapeFieldsFromStore(store, fallbackTimezone) {
    const a = store?.auto_scrape;
    const base = emptyAutoScrapeFields(fallbackTimezone);
    if (!a) return base;
    return {
        auto_scrape_enabled: !!a.enabled,
        auto_scrape_times: a.run_times?.length ? a.run_times : base.auto_scrape_times,
        auto_scrape_timezone: a.timezone || base.auto_scrape_timezone,
        auto_scrape_retry_cycles: a.retry_failed_cycles ?? base.auto_scrape_retry_cycles,
    };
}

export function validateAutoScrapeFields(form) {
    if (!form.auto_scrape_enabled) return '';
    const times = form.auto_scrape_times || [];
    if (!times.length) return 'Auto Scraping: add at least one run time.';
    if (new Set(times).size !== times.length) return 'Auto Scraping: each run time must be different.';
    const n = Number(form.auto_scrape_retry_cycles);
    if (!Number.isInteger(n) || n < 0 || n > AUTO_SCRAPE_MAX_CYCLES) {
        return `Auto Scraping: retry failed cycles must be 0 to ${AUTO_SCRAPE_MAX_CYCLES}.`;
    }
    return '';
}

export function buildAutoScrapePayload(form) {
    if (!form.auto_scrape_enabled) return { auto_scrape: { enabled: false } };
    return {
        auto_scrape: {
            enabled: true,
            run_times: [...new Set(form.auto_scrape_times || [])].sort(),
            timezone: form.auto_scrape_timezone,
            retry_failed_cycles: Number(form.auto_scrape_retry_cycles) || 0,
            // Pushing is left to each vendor's Continuous update / Scheduled Updates.
            push_after: false,
        },
    };
}

function formatLastRun(a) {
    if (!a?.last_run_at) return '';
    const when = new Date(a.last_run_at).toLocaleString();
    const s = a.last_summary || {};
    const trail = Array.isArray(s.failed_after) && s.failed_after.length ? s.failed_after.join(' → ') : null;
    const status = {
        running: 'running now',
        success: 'finished, nothing failed',
        partial: 'finished',
        stopped: 'stopped',
        skipped: 'skipped (a scrape was already running)',
        failed: 'failed',
    }[a.last_status] || a.last_status || '';
    return [
        `Last run ${when}`,
        status,
        s.cycles_run != null && a.last_status !== 'skipped' ? `${s.cycles_run} retry cycle(s)` : null,
        trail ? `failed: ${trail}` : null,
    ].filter(Boolean).join(' · ');
}

export default function AutoScrapeCard({ form, setForm, timezoneOptions, lastRun = null, storeActive = true, className = '' }) {
    const times = form.auto_scrape_times || [];
    const set = (patch) => setForm((f) => ({ ...f, ...patch }));
    const setTime = (idx, part, value) => {
        const next = times.map((t, i) => {
            if (i !== idx) return t;
            const [h, m] = splitTime(t);
            return part === 'h' ? `${value}:${m}` : `${h}:${value}`;
        });
        set({ auto_scrape_times: next });
    };
    const addTime = () => {
        if (times.length >= AUTO_SCRAPE_MAX_TIMES) return;
        const used = new Set(times);
        let h = times.length ? (Number(splitTime(times[times.length - 1])[0]) + 6) % 24 : 6;
        let candidate = `${String(h).padStart(2, '0')}:00`;
        for (let i = 0; i < 24 && used.has(candidate); i += 1) {
            h = (h + 1) % 24;
            candidate = `${String(h).padStart(2, '0')}:00`;
        }
        set({ auto_scrape_times: [...times, candidate] });
    };
    const removeTime = (idx) => set({ auto_scrape_times: times.filter((_, i) => i !== idx) });
    const lastRunText = formatLastRun(lastRun);

    return (
        <div className={`rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/50 p-5 space-y-4 ${className}`}>
            <div className="flex items-center gap-3">
                <RefreshCw className="h-5 w-5 text-slate-500 dark:text-slate-400" />
                <div className="flex-1">
                    <h3 className="text-sm font-medium text-slate-900 dark:text-slate-100">Auto Scraping</h3>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                        Scrape vendors at set times and retry failed products
                    </p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                    <input
                        type="checkbox"
                        checked={!!form.auto_scrape_enabled}
                        onChange={(e) => set({ auto_scrape_enabled: e.target.checked })}
                        className="sr-only peer"
                        aria-label="Auto Scraping"
                    />
                    <div className="w-9 h-5 bg-slate-300 dark:bg-slate-600 peer-focus:ring-2 peer-focus:ring-accent-400 rounded-full peer peer-checked:after:translate-x-full peer-checked:rtl:after:-translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:inset-s-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-accent-500"></div>
                </label>
            </div>

            {form.auto_scrape_enabled && !storeActive && (
                <div role="alert" className="flex items-start gap-2 rounded-md border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
                    <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
                    <span>
                        This store is inactive, so Auto Scraping won&apos;t run until you turn the store on
                        (Store Settings → Active).
                    </span>
                </div>
            )}

            {form.auto_scrape_enabled && (
                <div className="space-y-4 pt-2">
                    <div>
                        <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">Run times</label>
                        <div className="space-y-2">
                            {times.map((t, idx) => {
                                const [h, m] = splitTime(t);
                                const minuteOptions = MINUTES.includes(m) ? MINUTES : [...MINUTES, m].sort();
                                return (
                                    <div key={idx} className="flex gap-2 items-center">
                                        <select value={h} onChange={(e) => setTime(idx, 'h', e.target.value)} className={SELECT_CLASS} aria-label={`Run time ${idx + 1} hour`}>
                                            {HOURS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                                        </select>
                                        <span className="text-slate-500 dark:text-slate-400">:</span>
                                        <select value={m} onChange={(e) => setTime(idx, 'm', e.target.value)} className={SELECT_CLASS} aria-label={`Run time ${idx + 1} minute`}>
                                            {minuteOptions.map((o) => <option key={o} value={o}>{o}</option>)}
                                        </select>
                                        {times.length > 1 && (
                                            <button
                                                type="button"
                                                onClick={() => removeTime(idx)}
                                                className="p-2 text-slate-400 hover:text-red-500"
                                                aria-label={`Remove run time ${idx + 1}`}
                                            >
                                                <Trash2 className="h-4 w-4" />
                                            </button>
                                        )}
                                    </div>
                                );
                            })}
                        </div>
                        {times.length < AUTO_SCRAPE_MAX_TIMES && (
                            <button
                                type="button"
                                onClick={addTime}
                                className="mt-2 inline-flex items-center gap-1 text-sm text-accent-600 hover:text-accent-700 dark:text-accent-400"
                            >
                                <Plus className="h-4 w-4" /> Add time
                            </button>
                        )}
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                        <Select
                            label="Timezone"
                            value={form.auto_scrape_timezone}
                            onChange={(e) => set({ auto_scrape_timezone: e.target.value })}
                            options={timezoneOptions}
                        />
                        <div>
                            <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">Retry failed cycles</label>
                            <input
                                type="number"
                                min={0}
                                max={AUTO_SCRAPE_MAX_CYCLES}
                                step={1}
                                value={form.auto_scrape_retry_cycles}
                                onChange={(e) => {
                                    const raw = e.target.value;
                                    set({ auto_scrape_retry_cycles: raw === '' ? '' : Math.max(0, Math.min(AUTO_SCRAPE_MAX_CYCLES, Math.trunc(Number(raw)))) });
                                }}
                                className="w-full rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 focus:border-accent-500 focus:ring-1 focus:ring-accent-500 outline-hidden"
                            />
                        </div>
                    </div>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                        At each run time all active listings are set to Pending and scraped. Then, up to this many times,
                        Failed listings are set back to Pending and scraped again. It stops early when nothing is Failed.
                    </p>
                </div>
            )}

            {lastRunText && (
                <p className="text-xs text-slate-500 dark:text-slate-400">{lastRunText}</p>
            )}
        </div>
    );
}
