import { useEffect, useRef, useState } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { ArrowRight, CalendarDays, Play, RefreshCw, Store, Truck } from 'lucide-react';
import { CTAButton, Container, GridBackground } from './primitives';
import { DEMO_STORES, DEMO_SUPPLIERS, SALES_MAILTO, SYNC_PHASES } from './landingContent';
import DashboardPreview from './DashboardPreview';
import { useI18n } from '../../context/I18nContext';

function FlowNode({ icon: Icon, label, active }) {
    return (
        <div className="flex flex-col items-center gap-1.5">
            <span
                className={`grid h-10 w-10 place-items-center rounded-xl border text-sky-300 transition-colors ${
                    active
                        ? 'border-sky-400/60 bg-sky-400/15 shadow-[0_0_24px_-8px_rgba(56,189,248,0.9)]'
                        : 'border-white/10 bg-white/4'
                }`}
            >
                <Icon className="h-5 w-5" />
            </span>
            <span className={`text-[11px] font-medium ${active ? 'text-sky-200' : 'text-slate-400'}`}>{label}</span>
        </div>
    );
}

function FlowConnector({ delay = '0s' }) {
    return (
        <div className="relative h-6 w-[52px]" aria-hidden>
            <svg width="52" height="24" viewBox="0 0 52 24" fill="none" className="text-sky-400/60">
                <line x1="0" y1="12" x2="52" y2="12" stroke="currentColor" strokeWidth="2" className="spl-beam" />
            </svg>
            <span className="spl-packet" style={{ animationDelay: delay }} />
        </div>
    );
}

export default function HeroSection() {
    const reduce = useReducedMotion();
    const { t } = useI18n();
    const [supplier, setSupplier] = useState(DEMO_SUPPLIERS[0]);
    const [store, setStore] = useState(DEMO_STORES[0]);
    const [phase, setPhase] = useState('scrape');
    const [running, setRunning] = useState(false);
    const timers = useRef([]);

    const clearTimers = () => {
        timers.current.forEach(clearTimeout);
        timers.current = [];
    };

    useEffect(() => () => clearTimers(), []);

    useEffect(() => {
        if (reduce || running) return undefined;
        const id = setInterval(() => {
            setPhase((current) => SYNC_PHASES[(SYNC_PHASES.indexOf(current) + 1) % SYNC_PHASES.length]);
        }, 2400);
        return () => clearInterval(id);
    }, [reduce, running]);

    const runSync = () => {
        if (running) return;
        clearTimers();
        setRunning(true);
        setPhase('scrape');
        timers.current = [
            setTimeout(() => setPhase('rule'), 800),
            setTimeout(() => setPhase('push'), 1600),
            setTimeout(() => {
                setPhase('done');
                setRunning(false);
            }, 2500),
        ];
    };

    const activeNode = phase === 'scrape' ? 'supplier' : phase === 'rule' ? 'hub' : 'store';

    return (
        <section className="relative overflow-hidden border-b border-white/10 pt-28 pb-16 sm:pt-32 sm:pb-20">
            <div className="spl-aurora" aria-hidden />
            <GridBackground />

            <Container className="relative">
                <div className="grid items-center gap-12 lg:grid-cols-2 lg:gap-10">
                    <div className="max-w-xl">
                        <h1 className="font-display text-4xl font-semibold leading-[1.08] tracking-tight text-white sm:text-5xl lg:text-[3.4rem]">
                            {t('landing.headline')}
                        </h1>
                        <p className="mt-5 text-lg leading-relaxed text-slate-400">{t('landing.subhead')}</p>
                        <div className="mt-8 flex flex-wrap items-center gap-3">
                            <CTAButton to="/signup/super">
                                {t('landing.ctaPrimary')} <ArrowRight className="h-4 w-4" />
                            </CTAButton>
                            <CTAButton variant="secondary" href={SALES_MAILTO}>
                                <CalendarDays className="h-4 w-4" /> {t('landing.ctaSecondary')}
                            </CTAButton>
                        </div>
                        <p className="mt-4 text-sm text-slate-400">{t('landing.trust')}</p>

                        <div className="mt-9 inline-flex max-w-full flex-wrap items-center gap-2 rounded-2xl border border-white/10 bg-[#0b1120] px-4 py-3">
                            <FlowNode icon={Truck} label={supplier} active={activeNode === 'supplier'} />
                            <FlowConnector />
                            <FlowNode icon={RefreshCw} label={t('landing.flowHub')} active={activeNode === 'hub'} />
                            <FlowConnector delay="0.8s" />
                            <FlowNode icon={Store} label={store} active={activeNode === 'store'} />
                        </div>

                        <div className="mt-4 rounded-2xl border border-white/10 bg-[#0b1120]/80 p-4">
                            <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
                                <label className="block text-[11px] font-medium uppercase tracking-wide text-slate-400">
                                    {t('landing.sync.supplier')}
                                    <select
                                        value={supplier}
                                        onChange={(event) => setSupplier(event.target.value)}
                                        className="mt-1.5 w-full rounded-xl border border-white/10 bg-slate-950 px-3 py-2.5 text-sm font-medium normal-case tracking-normal text-white outline-none focus:border-sky-400"
                                    >
                                        {DEMO_SUPPLIERS.map((name) => (
                                            <option key={name} value={name}>
                                                {name}
                                            </option>
                                        ))}
                                    </select>
                                </label>
                                <label className="block text-[11px] font-medium uppercase tracking-wide text-slate-400">
                                    {t('landing.sync.store')}
                                    <select
                                        value={store}
                                        onChange={(event) => setStore(event.target.value)}
                                        className="mt-1.5 w-full rounded-xl border border-white/10 bg-slate-950 px-3 py-2.5 text-sm font-medium normal-case tracking-normal text-white outline-none focus:border-sky-400"
                                    >
                                        {DEMO_STORES.map((name) => (
                                            <option key={name} value={name}>
                                                {name}
                                            </option>
                                        ))}
                                    </select>
                                </label>
                                <CTAButton onClick={runSync} disabled={running} className="w-full sm:w-auto">
                                    <Play className={`h-4 w-4 ${running ? 'animate-pulse' : ''}`} />
                                    {running ? t('landing.sync.running') : t('landing.sync.run')}
                                </CTAButton>
                            </div>
                            <p className="mt-3 text-xs text-sky-300" aria-live="polite">
                                {t(`landing.sync.${phase}`)} · {supplier} → {store}
                            </p>
                        </div>
                    </div>

                    <div className="relative lg:pl-6">
                        <motion.div
                            initial={reduce ? false : { opacity: 0, y: 24 }}
                            animate={reduce ? {} : { opacity: 1, y: 0 }}
                            transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
                        >
                            <DashboardPreview supplier={supplier} store={store} phase={reduce ? 'done' : phase} hot={running} />
                        </motion.div>
                    </div>
                </div>
            </Container>
        </section>
    );
}
