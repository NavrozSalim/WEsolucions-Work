import {
    Activity,
    AlertTriangle,
    ArrowRight,
    Boxes,
    CheckCircle2,
    RefreshCw,
    Store,
    TrendingUp,
} from 'lucide-react';

const STATS = [
    { label: 'Connected stores', value: '8', icon: Store, tone: 'text-sky-300' },
    { label: 'Active products', value: '12,480', icon: Boxes, tone: 'text-cyan-300' },
    { label: 'Updated today', value: '247', icon: RefreshCw, tone: 'text-emerald-300' },
    { label: 'Failed syncs', value: '3', icon: AlertTriangle, tone: 'text-amber-300' },
];

const ACTIVITY = [
    { text: 'Price updated · Amazon', time: '2m', ok: true },
    { text: 'Inventory synced · eBay', time: '6m', ok: true },
    { text: 'Supplier price changed', time: '11m', ok: true },
    { text: 'Sync failed · Walmart', time: '18m', ok: false },
];

const HEALTH = [
    { name: 'Amazon', value: 98 },
    { name: 'eBay', value: 94 },
    { name: 'Walmart', value: 72 },
];

// Reusable, component-built dashboard mock (no stock imagery).
export default function DashboardPreview() {
    return (
        <div className="overflow-hidden rounded-2xl border border-sky-400/15 bg-[#121a2b]/95 shadow-2xl">
            {/* Window chrome */}
            <div className="flex items-center gap-2 border-b border-white/10 bg-white/[0.03] px-4 py-3">
                <span className="h-3 w-3 rounded-full bg-red-400/70" />
                <span className="h-3 w-3 rounded-full bg-amber-400/70" />
                <span className="h-3 w-3 rounded-full bg-emerald-400/70" />
                <div className="ml-3 flex items-center gap-2 rounded-md bg-white/5 px-2.5 py-1 text-[11px] text-slate-400">
                    <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-400 spl-pulse-dot" />
                    app.sellerpilothub.com
                </div>
            </div>

            <div className="p-4 sm:p-5">
                {/* Stat cards */}
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                    {STATS.map((s) => {
                        const Icon = s.icon;
                        return (
                            <div key={s.label} className="rounded-xl border border-white/10 bg-slate-900/45 p-3">
                                <div className="flex items-center justify-between">
                                    <Icon className={`h-4 w-4 ${s.tone}`} />
                                </div>
                                <div className="mt-2 font-display text-xl font-semibold text-white">{s.value}</div>
                                <div className="text-[11px] text-slate-400">{s.label}</div>
                            </div>
                        );
                    })}
                </div>

                <div className="mt-4 grid gap-3 lg:grid-cols-5">
                    {/* Recent activity */}
                    <div className="rounded-xl border border-white/10 bg-slate-900/45 p-4 lg:col-span-3">
                        <div className="flex items-center gap-2 text-xs font-medium text-slate-300">
                            <Activity className="h-4 w-4 text-sky-300" /> Recent activity
                        </div>
                        <ul className="mt-3 space-y-2.5">
                            {ACTIVITY.map((a) => (
                                <li key={a.text} className="flex items-center justify-between text-xs">
                                    <span className="flex items-center gap-2 text-slate-300">
                                        {a.ok ? (
                                            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
                                        ) : (
                                            <AlertTriangle className="h-3.5 w-3.5 text-amber-400" />
                                        )}
                                        {a.text}
                                    </span>
                                    <span className="tabular-nums text-slate-500">{a.time}</span>
                                </li>
                            ))}
                        </ul>
                    </div>

                    {/* Marketplace health + sync progress */}
                    <div className="space-y-3 lg:col-span-2">
                        <div className="rounded-xl border border-white/10 bg-slate-900/45 p-4">
                            <div className="flex items-center gap-2 text-xs font-medium text-slate-300">
                                <TrendingUp className="h-4 w-4 text-cyan-300" /> Marketplace health
                            </div>
                            <div className="mt-3 space-y-2.5">
                                {HEALTH.map((h) => (
                                    <div key={h.name}>
                                        <div className="flex items-center justify-between text-[11px] text-slate-400">
                                            <span>{h.name}</span>
                                            <span className="tabular-nums">{h.value}%</span>
                                        </div>
                                        <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-white/10">
                                            <div
                                                className="h-full rounded-full bg-gradient-to-r from-sky-400 to-cyan-400"
                                                style={{ width: `${h.value}%` }}
                                            />
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>

                        <div className="rounded-xl border border-white/10 bg-slate-900/45 p-4">
                            <div className="flex items-center justify-between text-xs text-slate-300">
                                <span className="flex items-center gap-2">
                                    <RefreshCw className="h-4 w-4 text-emerald-300" /> Sync progress
                                </span>
                                <span className="tabular-nums text-slate-400">78%</span>
                            </div>
                            <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-white/10">
                                <div className="h-full w-[78%] rounded-full bg-gradient-to-r from-emerald-400 to-teal-400" />
                            </div>
                            <div className="mt-3 flex items-center justify-between rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2 text-[11px]">
                                <span className="text-slate-400">Pricing rule</span>
                                <span className="inline-flex items-center gap-1 rounded-md bg-emerald-400/10 px-2 py-0.5 font-medium text-emerald-300">
                                    Active <ArrowRight className="h-3 w-3" />
                                </span>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
