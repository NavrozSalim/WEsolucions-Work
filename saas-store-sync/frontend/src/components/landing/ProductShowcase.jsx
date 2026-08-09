import { useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { CheckCircle2, Clock, ExternalLink, Loader2, TrendingDown, TrendingUp, XCircle } from 'lucide-react';
import { Container, Reveal, Section, SectionHeading } from './primitives';
import DashboardPreview from './DashboardPreview';
import { useI18n } from '../../context/I18nContext';

const CATALOG_ROWS = [
    { product: 'Wireless Earbuds Pro', supplier: 'vevor.au/…', sp: '$18.40', mp: '$34.99', inv: 128, status: 'synced', rule: '+90% margin' },
    { product: 'Studio Microphone X', supplier: 'reverb.com/…', sp: '$62.10', mp: '$119.00', inv: 24, status: 'pending', rule: 'Fixed +$45' },
    { product: 'Garden Tool Set 12pc', supplier: 'heb.com/…', sp: '$21.75', mp: '$41.50', inv: 0, status: 'failed', rule: '+90% margin' },
    { product: 'LED Desk Lamp', supplier: 'kogan.com/…', sp: '$9.20', mp: '$19.99', inv: 340, status: 'synced', rule: 'Min $18.99' },
];

const STATUS_MAP = {
    synced: { icon: CheckCircle2, cls: 'text-emerald-300 bg-emerald-400/10 border-emerald-400/20', label: 'Synced' },
    pending: { icon: Loader2, cls: 'text-sky-300 bg-sky-400/10 border-sky-400/20', label: 'Pending' },
    failed: { icon: XCircle, cls: 'text-red-300 bg-red-400/10 border-red-400/20', label: 'Failed' },
};

function StatusPill({ status }) {
    const s = STATUS_MAP[status];
    const Icon = s.icon;
    return (
        <span className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-medium ${s.cls}`}>
            <Icon className={`h-3 w-3 ${status === 'pending' ? 'animate-spin' : ''}`} /> {s.label}
        </span>
    );
}

function CatalogPreview() {
    return (
        <div className="overflow-x-auto rounded-xl border border-white/10">
            <table className="w-full min-w-[720px] border-collapse text-left text-sm">
                <thead>
                    <tr className="border-b border-white/10 bg-white/[0.03] text-[11px] uppercase tracking-wide text-slate-400">
                        <th className="px-4 py-3 font-medium">Product</th>
                        <th className="px-4 py-3 font-medium">Supplier URL</th>
                        <th className="px-4 py-3 font-medium">Supplier</th>
                        <th className="px-4 py-3 font-medium">Marketplace</th>
                        <th className="px-4 py-3 font-medium">Inventory</th>
                        <th className="px-4 py-3 font-medium">Sync</th>
                        <th className="px-4 py-3 font-medium">Pricing rule</th>
                    </tr>
                </thead>
                <tbody className="divide-y divide-white/5">
                    {CATALOG_ROWS.map((r) => (
                        <tr key={r.product} className="text-slate-300">
                            <td className="px-4 py-3 font-medium text-white">{r.product}</td>
                            <td className="px-4 py-3">
                                <span className="inline-flex items-center gap-1 text-slate-400">
                                    {r.supplier} <ExternalLink className="h-3 w-3" />
                                </span>
                            </td>
                            <td className="px-4 py-3 tabular-nums text-slate-400">{r.sp}</td>
                            <td className="px-4 py-3 tabular-nums font-medium text-white">{r.mp}</td>
                            <td className="px-4 py-3 tabular-nums">
                                <span className={r.inv === 0 ? 'text-red-300' : 'text-slate-300'}>{r.inv}</span>
                            </td>
                            <td className="px-4 py-3"><StatusPill status={r.status} /></td>
                            <td className="px-4 py-3 text-slate-400">{r.rule}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

const SYNC_JOBS = [
    { name: 'Amazon price update', state: 'success', dur: '1m 12s', icon: CheckCircle2, tone: 'text-emerald-300' },
    { name: 'eBay inventory sync', state: 'success', dur: '48s', icon: CheckCircle2, tone: 'text-emerald-300' },
    { name: 'Walmart listing push', state: 'running', dur: '00:34', icon: Loader2, tone: 'text-sky-300' },
    { name: 'Supplier scrape · Vevor', state: 'queued', dur: '—', icon: Clock, tone: 'text-slate-400' },
    { name: 'HEB ingest', state: 'failed', dur: '2m 03s', icon: XCircle, tone: 'text-red-300' },
];

function SyncActivityPreview() {
    return (
        <div className="rounded-xl border border-white/10 divide-y divide-white/5">
            {SYNC_JOBS.map((j) => {
                const Icon = j.icon;
                return (
                    <div key={j.name} className="flex items-center justify-between px-4 py-3.5">
                        <span className="flex items-center gap-2.5 text-sm text-slate-200">
                            <Icon className={`h-4 w-4 ${j.tone} ${j.state === 'running' ? 'animate-spin' : ''}`} />
                            {j.name}
                        </span>
                        <span className="flex items-center gap-3 text-xs text-slate-500">
                            <span className="capitalize">{j.state}</span>
                            <span className="tabular-nums">{j.dur}</span>
                        </span>
                    </div>
                );
            })}
        </div>
    );
}

const ANALYTICS_BARS = [42, 58, 51, 73, 66, 88, 79];
function AnalyticsPreview() {
    return (
        <div className="grid gap-4 sm:grid-cols-3">
            <div className="rounded-xl border border-white/10 bg-white/[0.02] p-5 sm:col-span-2">
                <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-slate-200">Updates per day</span>
                    <span className="inline-flex items-center gap-1 text-xs text-emerald-300">
                        <TrendingUp className="h-3.5 w-3.5" /> +18%
                    </span>
                </div>
                <div className="mt-6 flex h-40 items-end gap-3">
                    {ANALYTICS_BARS.map((h, i) => (
                        <div key={i} className="flex-1">
                            <div
                                className="w-full rounded-t-md bg-gradient-to-t from-sky-500/40 to-cyan-400/80"
                                style={{ height: `${h}%` }}
                            />
                        </div>
                    ))}
                </div>
            </div>
            <div className="space-y-4">
                {[
                    { label: 'Avg. margin', value: '41%', icon: TrendingUp, tone: 'text-emerald-300' },
                    { label: 'Failed syncs', value: '1.2%', icon: TrendingDown, tone: 'text-emerald-300' },
                    { label: 'Products tracked', value: '12,480', icon: TrendingUp, tone: 'text-sky-300' },
                ].map((s) => {
                    const Icon = s.icon;
                    return (
                        <div key={s.label} className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
                            <div className="flex items-center justify-between">
                                <span className="text-xs text-slate-400">{s.label}</span>
                                <Icon className={`h-4 w-4 ${s.tone}`} />
                            </div>
                            <div className="mt-1 font-display text-xl font-semibold text-white">{s.value}</div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

const TEAM_ROWS = [
    { name: 'Alex Morgan', role: 'Super User', modules: 'All modules' },
    { name: 'Priya Shah', role: 'Catalog manager', modules: 'Dashboard, Catalog, Orders' },
    { name: 'Diego Ruiz', role: 'Support', modules: 'Dashboard, Tickets' },
];
function TeamAccessPreview() {
    return (
        <div className="rounded-xl border border-white/10 divide-y divide-white/5">
            {TEAM_ROWS.map((m) => (
                <div key={m.name} className="flex items-center justify-between px-4 py-4">
                    <div className="flex items-center gap-3">
                        <span className="grid h-9 w-9 place-items-center rounded-full bg-gradient-to-br from-sky-400/30 to-cyan-400/20 text-xs font-semibold text-sky-200">
                            {m.name.split(' ').map((n) => n[0]).join('')}
                        </span>
                        <div>
                            <div className="text-sm font-medium text-white">{m.name}</div>
                            <div className="text-xs text-slate-500">{m.role}</div>
                        </div>
                    </div>
                    <span className="hidden text-xs text-slate-400 sm:block">{m.modules}</span>
                </div>
            ))}
        </div>
    );
}

function TabPanel({ tab }) {
    switch (tab) {
        case 'catalog':
            return <CatalogPreview />;
        case 'sync':
            return <SyncActivityPreview />;
        case 'analytics':
            return <AnalyticsPreview />;
        case 'team':
            return <TeamAccessPreview />;
        default:
            return <DashboardPreview />;
    }
}

export default function ProductShowcase() {
    const { t, locale } = useI18n();
    const isEs = locale === 'es';
    const tabs = [
        { id: 'overview', label: isEs ? 'Resumen' : 'Overview' },
        { id: 'catalog', label: isEs ? 'Catálogo' : 'Catalog' },
        { id: 'sync', label: isEs ? 'Actividad de sync' : 'Sync Activity' },
        { id: 'analytics', label: isEs ? 'Analítica' : 'Analytics' },
        { id: 'team', label: isEs ? 'Acceso de equipo' : 'Team Access' },
    ];
    const [tab, setTab] = useState('overview');
    const reduce = useReducedMotion();

    return (
        <Section>
            <Container>
                <Reveal>
                    <SectionHeading
                        eyebrow={t('landing.showcase.eyebrow')}
                        title={t('landing.showcase.title')}
                        subtitle={t('landing.showcase.subtitle')}
                    />
                </Reveal>

                <Reveal delay={0.1}>
                    <div className="mt-10 rounded-2xl border border-white/10 bg-[#0b1120] p-4 sm:p-6">
                        <div
                            role="tablist"
                            aria-label={isEs ? 'Vistas del panel' : 'Dashboard views'}
                            className="flex flex-wrap gap-1.5 rounded-xl border border-white/10 bg-white/[0.02] p-1.5"
                        >
                            {tabs.map((item) => (
                                <button
                                    key={item.id}
                                    role="tab"
                                    aria-selected={tab === item.id}
                                    onClick={() => setTab(item.id)}
                                    className={`relative rounded-lg px-3.5 py-2 text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 ${
                                        tab === item.id ? 'text-white' : 'text-slate-400 hover:text-slate-200'
                                    }`}
                                >
                                    {tab === t ? (
                                        <motion.span
                                            layoutId="spl-tab-pill"
                                            className="absolute inset-0 rounded-lg bg-sky-500/15 ring-1 ring-inset ring-sky-400/30"
                                            transition={{ type: 'spring', stiffness: 400, damping: 32 }}
                                        />
                                    ) : null}
                                    <span className="relative z-10">{item.label}</span>
                                </button>
                            ))}
                        </div>

                        <div className="mt-5">
                            <AnimatePresence mode="wait">
                                <motion.div
                                    key={tab}
                                    initial={reduce ? false : { opacity: 0, y: 8 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    exit={reduce ? {} : { opacity: 0, y: -8 }}
                                    transition={{ duration: 0.25 }}
                                >
                                    <TabPanel tab={tab} />
                                </motion.div>
                            </AnimatePresence>
                        </div>
                    </div>
                </Reveal>
            </Container>
        </Section>
    );
}
