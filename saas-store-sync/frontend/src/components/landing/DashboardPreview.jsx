import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import {
    Activity,
    AlertTriangle,
    CheckCircle2,
    Clock3,
    Package,
    PackageX,
    Store,
} from 'lucide-react';
import { DEMO_STORES, DEMO_SUPPLIERS, SYNC_PHASES } from './landingContent';
import { useI18n } from '../../context/I18nContext';

const HEALTH_FALLBACK = ['Reverb', 'Bunnings', 'MyDeal', 'Temu'];

const STATUS_TONE = {
    healthy: 'border-emerald-400/30 bg-emerald-400/10 text-emerald-300',
    pending: 'border-amber-400/30 bg-amber-400/10 text-amber-300',
    syncing: 'border-sky-400/30 bg-sky-400/10 text-sky-300',
    attention: 'border-rose-400/30 bg-rose-400/10 text-rose-300',
};

function statusKey(name, activeStore, phase) {
    if (name === activeStore) {
        if (phase === 'done') return 'healthy';
        if (phase === 'push' || phase === 'rule') return 'syncing';
        return 'pending';
    }
    if (name === 'MyDeal') return 'attention';
    return 'healthy';
}

function visibleStores(activeStore) {
    return [activeStore, ...HEALTH_FALLBACK.filter((name) => name !== activeStore)].slice(0, 4);
}

function progressFor(phase) {
    return { scrape: 32, rule: 64, push: 86, done: 100 }[phase] ?? 32;
}

export default function DashboardPreview({ supplier: supplierProp, store: storeProp, phase: phaseProp, hot = false }) {
    const reduce = useReducedMotion();
    const { t } = useI18n();
    const controlled = phaseProp != null;
    const [localPhase, setLocalPhase] = useState('scrape');
    const [pairIndex, setPairIndex] = useState(0);
    const [log, setLog] = useState(() => {
        const initialSupplier = supplierProp || DEMO_SUPPLIERS[0];
        return [
            { id: 'seed-1', text: t('landing.sync.lineScrape', { name: initialSupplier }), ok: true, time: t('landing.sync.now') },
            { id: 'seed-2', text: t('landing.sync.linePush', { name: 'Reverb' }), ok: true, time: '2m' },
            { id: 'seed-3', text: t('landing.sync.lineDone', { name: 'Temu' }), ok: true, time: '6m' },
            { id: 'seed-4', text: t('landing.sync.lineRule', { name: 'Etsy' }), ok: true, time: '11m' },
        ];
    });
    const seq = useRef(0);
    const tiltRef = useRef(null);
    const [tilt, setTilt] = useState('perspective(900px) rotateX(0deg) rotateY(0deg)');

    const supplier = supplierProp || DEMO_SUPPLIERS[pairIndex % DEMO_SUPPLIERS.length];
    const store = storeProp || DEMO_STORES[pairIndex % DEMO_STORES.length];
    const phase = controlled ? phaseProp : reduce ? 'done' : localPhase;

    useEffect(() => {
        if (controlled || reduce) return undefined;
        const id = setInterval(() => {
            setLocalPhase((current) => {
                const next = SYNC_PHASES[(SYNC_PHASES.indexOf(current) + 1) % SYNC_PHASES.length];
                if (current === 'done') setPairIndex((index) => index + 1);
                return next;
            });
        }, 2200);
        return () => clearInterval(id);
    }, [controlled, reduce]);

    useEffect(() => {
        const text =
            phase === 'scrape'
                ? t('landing.sync.lineScrape', { name: supplier })
                : phase === 'rule'
                  ? t('landing.sync.lineRule', { name: store })
                  : phase === 'push'
                    ? t('landing.sync.linePush', { name: store })
                    : t('landing.sync.lineDone', { name: store });
        setLog((prev) => {
            if (prev[0]?.text === text) return prev;
            const id = ++seq.current;
            const times = [t('landing.sync.now'), '2m', '6m', '11m'];
            return [{ id, text, ok: true }, ...prev].slice(0, 4).map((row, index) => ({
                ...row,
                time: times[index],
            }));
        });
    }, [phase, supplier, store, t]);

    const onMove = (event) => {
        if (reduce || !tiltRef.current) return;
        const rect = tiltRef.current.getBoundingClientRect();
        const x = (event.clientX - rect.left) / rect.width - 0.5;
        const y = (event.clientY - rect.top) / rect.height - 0.5;
        setTilt(`perspective(900px) rotateY(${x * 8}deg) rotateX(${-y * 8}deg)`);
    };

    const stats = [
        { label: t('landing.sync.needsAttention'), value: '3', icon: AlertTriangle, tone: 'text-rose-300' },
        { label: t('landing.sync.pending'), value: phase === 'done' ? '17' : '18', icon: Clock3, tone: 'text-amber-300' },
        { label: t('landing.sync.outOfStock'), value: '6', icon: PackageX, tone: 'text-amber-300' },
        { label: t('landing.sync.activeListings'), value: '1,240', icon: Package, tone: 'text-sky-300' },
    ];
    const progress = progressFor(phase);
    const statusLabel = {
        healthy: t('landing.sync.healthy'),
        pending: t('landing.sync.pending'),
        syncing: t('landing.sync.syncing'),
        attention: t('landing.sync.attention'),
    };

    return (
        <div
            ref={tiltRef}
            onMouseMove={onMove}
            onMouseLeave={() => setTilt('perspective(900px) rotateX(0deg) rotateY(0deg)')}
            style={{ transform: tilt }}
            className={`overflow-hidden rounded-2xl border border-sky-400/20 bg-[#121a2b]/95 shadow-2xl transition-transform duration-200 ease-out will-change-transform ${
                hot ? 'spl-shimmer-border' : ''
            }`}
        >
            <div className="flex items-center gap-2 border-b border-white/10 bg-white/3 px-4 py-3">
                <span className="h-3 w-3 rounded-full bg-red-400/70" />
                <span className="h-3 w-3 rounded-full bg-amber-400/70" />
                <span className="h-3 w-3 rounded-full bg-emerald-400/70" />
                <div className="ml-3 flex items-center gap-2 rounded-md bg-white/5 px-2.5 py-1 text-[11px] text-slate-400">
                    <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-400 spl-pulse-dot" />
                    app.sellerpilothub.com
                </div>
                <span className="ml-auto text-[10px] uppercase tracking-wide text-slate-500">{t('landing.sync.preview')}</span>
            </div>

            <div className="p-4 sm:p-5">
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                    {stats.map((stat) => {
                        const Icon = stat.icon;
                        return (
                            <div key={stat.label} className="rounded-xl border border-white/10 bg-slate-900/45 p-3">
                                <Icon className={`h-4 w-4 ${stat.tone}`} />
                                <div className="mt-2 font-display text-xl font-semibold text-white">{stat.value}</div>
                                <div className="text-[11px] text-slate-400">{stat.label}</div>
                            </div>
                        );
                    })}
                </div>

                <div className="mt-4 grid gap-3 lg:grid-cols-5">
                    <div className="rounded-xl border border-white/10 bg-slate-900/45 p-4 lg:col-span-3">
                        <div className="flex items-center gap-2 text-xs font-medium text-slate-300">
                            <Activity className="h-4 w-4 text-sky-300" /> {t('landing.sync.recent')}
                        </div>
                        <ul className="mt-3 space-y-2">
                            <AnimatePresence initial={false}>
                                {log.map((row) => (
                                    <motion.li
                                        key={row.id}
                                        initial={reduce ? false : { opacity: 0, y: -8 }}
                                        animate={{ opacity: 1, y: 0 }}
                                        exit={reduce ? {} : { opacity: 0 }}
                                        transition={{ duration: 0.25 }}
                                        className="flex items-center justify-between text-xs"
                                    >
                                        <span className="flex min-w-0 items-center gap-2 text-slate-300">
                                            {row.ok ? (
                                                <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-400" />
                                            ) : (
                                                <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-400" />
                                            )}
                                            <span className="truncate">{row.text}</span>
                                        </span>
                                        <span className="ml-3 shrink-0 tabular-nums text-slate-500">{row.time}</span>
                                    </motion.li>
                                ))}
                            </AnimatePresence>
                        </ul>
                    </div>

                    <div className="space-y-3 lg:col-span-2">
                        <div className="rounded-xl border border-white/10 bg-slate-900/45 p-4">
                            <div className="flex items-center gap-2 text-xs font-medium text-slate-300">
                                <Store className="h-4 w-4 text-cyan-300" /> {t('landing.sync.storeHealth')}
                            </div>
                            <ul className="mt-3 space-y-2">
                                {visibleStores(store).map((name) => {
                                    const key = statusKey(name, store, phase);
                                    return (
                                        <li key={name} className="flex items-center justify-between gap-2 text-[11px]">
                                            <span className="min-w-0 truncate text-slate-200">{name}</span>
                                            <span className={`shrink-0 rounded-full border px-2 py-0.5 font-medium ${STATUS_TONE[key]}`}>
                                                {statusLabel[key]}
                                            </span>
                                        </li>
                                    );
                                })}
                            </ul>
                        </div>

                        <div className="rounded-xl border border-white/10 bg-slate-900/45 p-4">
                            <div className="flex items-center justify-between text-xs text-slate-300">
                                <span>{t('landing.sync.syncProgress')}</span>
                                <span className="tabular-nums text-slate-400">{progress}%</span>
                            </div>
                            <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-white/10">
                                <div
                                    className="h-full rounded-full bg-linear-to-r from-sky-400 to-emerald-400 transition-[width] duration-700 ease-out"
                                    style={{ width: `${progress}%` }}
                                />
                            </div>
                            <p className="mt-2 text-[11px] text-sky-300">{t(`landing.sync.${phase}`)}</p>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
