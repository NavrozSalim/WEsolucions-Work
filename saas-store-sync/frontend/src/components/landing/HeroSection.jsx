import { motion, useReducedMotion } from 'framer-motion';
import {
    ArrowRight,
    CalendarDays,
    CheckCircle2,
    PackageCheck,
    RefreshCw,
    Sparkles,
    Store,
    TrendingUp,
    Truck,
} from 'lucide-react';
import { CTAButton, Container, Eyebrow, GridBackground } from './primitives';
import { SALES_MAILTO } from './landingContent';
import DashboardPreview from './DashboardPreview';
import { useI18n } from '../../context/I18nContext';

const FLOATERS = [
    { icon: PackageCheck, text: '247 products updated', pos: '-left-4 top-14', delay: 0 },
    { icon: TrendingUp, text: 'Supplier price changed', pos: '-right-3 top-36', delay: 0.5 },
    { icon: CheckCircle2, text: 'Marketplace listing updated', pos: '-left-2 bottom-24', delay: 1 },
    { icon: RefreshCw, text: 'Inventory sync completed', pos: '-right-2 bottom-8', delay: 1.5 },
];

function FlowNode({ icon: Icon, label }) {
    return (
        <div className="flex flex-col items-center gap-1.5">
            <span className="grid h-10 w-10 place-items-center rounded-xl border border-white/10 bg-white/[0.04] text-sky-300">
                <Icon className="h-5 w-5" />
            </span>
            <span className="text-[11px] font-medium text-slate-400">{label}</span>
        </div>
    );
}

function FlowConnector() {
    return (
        <svg width="52" height="24" viewBox="0 0 52 24" fill="none" className="text-sky-400/60" aria-hidden>
            <line x1="0" y1="12" x2="52" y2="12" stroke="currentColor" strokeWidth="2" className="spl-beam" />
        </svg>
    );
}

export default function HeroSection() {
    const reduce = useReducedMotion();
    const { t } = useI18n();

    return (
        <section className="relative overflow-hidden pt-28 pb-16 sm:pt-32 sm:pb-20">
            <div className="spl-aurora" aria-hidden />
            <GridBackground />

            <Container className="relative">
                <div className="grid items-center gap-12 lg:grid-cols-2 lg:gap-10">
                    {/* Left */}
                    <div className="max-w-xl">
                        <Eyebrow>
                            <Sparkles className="h-3.5 w-3.5" /> {t('landing.badge')}
                        </Eyebrow>
                        <h1 className="mt-5 font-display text-4xl font-semibold leading-[1.08] tracking-tight text-white sm:text-5xl lg:text-[3.4rem]">
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
                        <p className="mt-4 text-sm text-slate-500">{t('landing.trust')}</p>

                        {/* Supplier -> Hub -> Marketplace flow */}
                        <div className="mt-9 inline-flex items-center gap-2 rounded-2xl border border-white/10 bg-white/[0.02] px-4 py-3">
                            <FlowNode icon={Truck} label={t('landing.flowSupplier')} />
                            <FlowConnector />
                            <FlowNode icon={RefreshCw} label={t('landing.flowHub')} />
                            <FlowConnector />
                            <FlowNode icon={Store} label={t('landing.flowMarketplace')} />
                        </div>
                    </div>

                    {/* Right */}
                    <div className="relative">
                        <motion.div
                            initial={reduce ? false : { opacity: 0, y: 24 }}
                            animate={reduce ? {} : { opacity: 1, y: 0 }}
                            transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
                        >
                            <DashboardPreview />
                        </motion.div>

                        {FLOATERS.map((f, i) => {
                            const Icon = f.icon;
                            return (
                                <motion.div
                                    key={f.text}
                                    className={`absolute ${f.pos} hidden items-center gap-2 rounded-xl border border-white/10 bg-[#0b1120]/95 px-3 py-2 shadow-xl backdrop-blur sm:flex ${
                                        i % 2 === 0 ? 'spl-float' : 'spl-float-slow'
                                    }`}
                                    initial={reduce ? false : { opacity: 0, scale: 0.9 }}
                                    animate={reduce ? {} : { opacity: 1, scale: 1 }}
                                    transition={{ duration: 0.5, delay: 0.4 + f.delay }}
                                >
                                    <span className="grid h-7 w-7 place-items-center rounded-lg bg-sky-400/15 text-sky-300">
                                        <Icon className="h-4 w-4" />
                                    </span>
                                    <span className="text-xs font-medium text-slate-200">{f.text}</span>
                                </motion.div>
                            );
                        })}
                    </div>
                </div>
            </Container>
        </section>
    );
}
