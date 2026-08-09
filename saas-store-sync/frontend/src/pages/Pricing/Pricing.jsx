import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import { ArrowRight, Check } from 'lucide-react';
import LanguageSwitcher from '../../components/layout/LanguageSwitcher';
import { BrandMark } from '../../components/landing/Navbar';
import { CTAButton, Container, GridBackground } from '../../components/landing/primitives';
import { SALES_MAILTO } from '../../components/landing/landingContent';
import { useI18n } from '../../context/I18nContext';
import { getPricingPlans } from '../../services/authService';

export default function Pricing() {
    const { t } = useI18n();
    const navigate = useNavigate();
    const [plans, setPlans] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const data = await getPricingPlans();
                if (!cancelled) setPlans(data.plans || []);
            } catch {
                if (!cancelled) {
                    setPlans([
                        { seats: 5, price_usd: 0, is_free: true },
                        { seats: 10, price_usd: 10, is_free: false },
                        { seats: 15, price_usd: 20, is_free: false },
                        { seats: 20, price_usd: 30, is_free: false },
                    ]);
                }
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => {
            cancelled = true;
        };
    }, []);

    const featured = [5, 10, 15, 20];
    const shown = (plans.length ? plans : []).filter((p) => featured.includes(p.seats));
    const display = loading
        ? featured.map((s) => ({ seats: s, price_usd: s === 5 ? 0 : ((s - 5) / 5) * 10, is_free: s === 5 }))
        : shown.length
          ? shown
          : featured.map((s) => ({ seats: s, price_usd: s === 5 ? 0 : ((s - 5) / 5) * 10, is_free: s === 5 }));
    const extras = plans.filter((p) => !featured.includes(p.seats));

    const featuresFor = (seats) => {
        if (seats <= 5) return ['All core modules', 'Background sync jobs', 'OTP-verified Super User'];
        if (seats <= 10) return ['Everything in Free', 'Granular team permissions', 'Audit visibility'];
        if (seats <= 15) return ['Everything in $10', 'Larger team capacity', 'Email support'];
        return ['Everything in $20', 'Bulk catalog workflows', 'Onboarding help'];
    };

    return (
        <div className="spl-page min-h-screen">
            <div className="spl-aurora" aria-hidden />
            <GridBackground />

            <header className="relative z-20 border-b border-white/10 bg-[#060910]/70 backdrop-blur-xl">
                <Container className="flex h-16 items-center justify-between">
                    <BrandMark />
                    <div className="flex items-center gap-3">
                        <LanguageSwitcher className="text-slate-200" />
                        <Link
                            to="/login/choose"
                            className="rounded-lg px-3 py-2 text-sm font-semibold text-slate-200 transition-colors hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
                        >
                            {t('nav.login')}
                        </Link>
                        <CTAButton to="/" variant="secondary" className="hidden px-4 py-2 sm:inline-flex">
                            {t('loginChooser.back')}
                        </CTAButton>
                    </div>
                </Container>
            </header>

            <main className="relative z-10">
                <Container className="py-14 sm:py-16">
                    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
                        <p className="inline-flex rounded-full border border-sky-400/25 bg-sky-400/10 px-3 py-1 text-xs font-medium uppercase tracking-wider text-sky-300">
                            {t('landing.pricing.eyebrow')}
                        </p>
                        <h1 className="mt-4 max-w-2xl font-display text-3xl font-semibold tracking-tight text-white sm:text-4xl">
                            {t('pricing.title')}
                        </h1>
                        <p className="mt-3 max-w-2xl text-base leading-relaxed text-slate-400">
                            {t('pricing.subtitle')}
                        </p>
                    </motion.div>

                    <div className="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
                        {display.map((plan, i) => {
                            const popular = plan.seats === 10;
                            return (
                                <motion.div
                                    key={plan.seats}
                                    initial={{ opacity: 0, y: 16 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    transition={{ delay: i * 0.06 }}
                                    className={`relative flex h-full flex-col rounded-2xl border p-6 ${
                                        popular
                                            ? 'spl-shimmer-border border-sky-400/40 bg-sky-500/[0.06]'
                                            : 'border-white/10 bg-white/[0.02]'
                                    }`}
                                >
                                    {popular ? (
                                        <span className="absolute -top-3 left-6 rounded-full bg-sky-500 px-3 py-0.5 text-[11px] font-semibold text-white">
                                            {t('pricing.popular')}
                                        </span>
                                    ) : null}
                                    <p className="text-sm font-medium text-slate-400">
                                        {plan.seats} {t('pricing.seats')}
                                    </p>
                                    <p className="mt-3 flex items-end gap-1">
                                        {plan.is_free || plan.price_usd === 0 ? (
                                            <span className="font-display text-4xl font-semibold text-white">
                                                {t('pricing.free')}
                                            </span>
                                        ) : (
                                            <>
                                                <span className="font-display text-4xl font-semibold text-white">
                                                    ${plan.price_usd}
                                                </span>
                                                <span className="mb-1 text-sm text-slate-500">{t('pricing.perMonth')}</span>
                                            </>
                                        )}
                                    </p>
                                    <ul className="mt-5 flex-1 space-y-2.5">
                                        {featuresFor(plan.seats).map((f) => (
                                            <li key={f} className="flex items-start gap-2 text-sm text-slate-300">
                                                <Check className="mt-0.5 h-4 w-4 shrink-0 text-sky-400" />
                                                {f}
                                            </li>
                                        ))}
                                    </ul>
                                    <button
                                        type="button"
                                        className={`mt-6 inline-flex w-full items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 ${
                                            popular
                                                ? 'bg-sky-500 text-white hover:bg-sky-400'
                                                : 'border border-white/15 bg-white/5 text-slate-100 hover:bg-white/10'
                                        }`}
                                        onClick={() =>
                                            navigate('/signup/super', {
                                                state: {
                                                    seat_limit: plan.seats,
                                                    plan_price_usd: plan.price_usd,
                                                },
                                            })
                                        }
                                    >
                                        {t('pricing.continue')} <ArrowRight className="h-4 w-4" />
                                    </button>
                                </motion.div>
                            );
                        })}
                    </div>

                    {extras.length > 0 ? (
                        <div className="mt-10">
                            <p className="text-sm font-medium text-slate-300">{t('pricing.morePacks')}</p>
                            <div className="mt-3 flex flex-wrap gap-2">
                                {extras.map((plan) => (
                                    <button
                                        key={plan.seats}
                                        type="button"
                                        onClick={() =>
                                            navigate('/signup/super', {
                                                state: {
                                                    seat_limit: plan.seats,
                                                    plan_price_usd: plan.price_usd,
                                                },
                                            })
                                        }
                                        className="rounded-full border border-white/10 bg-white/[0.03] px-3.5 py-2 text-sm font-medium text-slate-200 transition-colors hover:border-sky-400/40 hover:bg-sky-400/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
                                    >
                                        {plan.seats} — ${plan.price_usd}
                                    </button>
                                ))}
                            </div>
                        </div>
                    ) : null}

                    <div className="mt-8 flex flex-col items-start justify-between gap-4 rounded-2xl border border-white/10 bg-white/[0.02] p-6 sm:flex-row sm:items-center">
                        <div>
                            <p className="font-display text-base font-semibold text-white">{t('pricing.enterprise')}</p>
                            <p className="mt-1 text-sm text-slate-400">{t('landing.pricing.enterpriseBody')}</p>
                        </div>
                        <a
                            href={SALES_MAILTO}
                            className="inline-flex shrink-0 items-center gap-2 rounded-xl border border-white/15 bg-white/5 px-4 py-2.5 text-sm font-semibold text-slate-100 transition-colors hover:bg-white/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
                        >
                            {t('pricing.contactSales')} <ArrowRight className="h-4 w-4" />
                        </a>
                    </div>

                    <p className="mt-8 text-sm text-slate-500">{t('pricing.note')}</p>
                </Container>
            </main>
        </div>
    );
}
