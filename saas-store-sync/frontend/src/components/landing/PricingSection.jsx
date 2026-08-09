import { useNavigate } from 'react-router-dom';
import { ArrowRight, Building2, Check } from 'lucide-react';
import { Container, Reveal, Section, SectionHeading } from './primitives';
import { PRICING_PLANS, SALES_MAILTO } from './landingContent';
import { useI18n } from '../../context/I18nContext';

export default function PricingSection() {
    const { t } = useI18n();
    const navigate = useNavigate();

    const startPlan = (plan) =>
        navigate('/signup/super', {
            state: { seat_limit: plan.seats, plan_price_usd: plan.price },
        });

    return (
        <Section id="pricing">
            <Container>
                <Reveal>
                    <SectionHeading
                        eyebrow={t('landing.pricing.eyebrow')}
                        title={t('landing.pricing.title')}
                        subtitle={t('landing.pricing.subtitle')}
                    />
                </Reveal>

                <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
                    {PRICING_PLANS.map((plan, i) => (
                        <Reveal key={plan.seats} delay={i * 0.06}>
                            <div
                                className={`relative flex h-full flex-col rounded-2xl border p-6 ${
                                    plan.highlight
                                        ? 'border-sky-400/45 bg-sky-500/[0.08]'
                                        : 'border-white/10 bg-white/[0.02]'
                                }`}
                            >
                                {plan.highlight ? (
                                    <span className="absolute -top-3 left-6 rounded-full bg-sky-500 px-3 py-0.5 text-[11px] font-semibold text-white">
                                        {t('landing.pricing.mostPopular')}
                                    </span>
                                ) : null}
                                <div className="text-sm font-medium text-slate-400">
                                    {t('landing.pricing.seats', { count: plan.seats })}
                                </div>
                                <div className="mt-2 flex items-end gap-1">
                                    <span className="font-display text-4xl font-semibold text-white">
                                        {plan.price === 0 ? t('pricing.free') : plan.priceLabel}
                                    </span>
                                    {plan.price > 0 ? (
                                        <span className="mb-1 text-sm text-slate-500">{t('landing.pricing.perMonth')}</span>
                                    ) : null}
                                </div>
                                <p className="mt-1 text-xs text-slate-500">{plan.tagline}</p>

                                <ul className="mt-5 flex-1 space-y-2.5">
                                    {plan.features.map((f) => (
                                        <li key={f} className="flex items-start gap-2 text-sm text-slate-300">
                                            <Check className="mt-0.5 h-4 w-4 shrink-0 text-sky-400" />
                                            {f}
                                        </li>
                                    ))}
                                </ul>

                                <button
                                    type="button"
                                    onClick={() => startPlan(plan)}
                                    className={`mt-6 inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 focus-visible:ring-offset-2 focus-visible:ring-offset-[#060910] ${
                                        plan.highlight
                                            ? 'bg-sky-500 text-white hover:bg-sky-400'
                                            : 'border border-white/15 bg-white/5 text-slate-100 hover:bg-white/10'
                                    }`}
                                >
                                    {plan.price === 0
                                        ? t('nav.startFree')
                                        : plan.highlight
                                          ? t('landing.pricing.choosePlan')
                                          : t('landing.pricing.startSetup')}{' '}
                                    <ArrowRight className="h-4 w-4" />
                                </button>
                            </div>
                        </Reveal>
                    ))}
                </div>

                {/* Enterprise */}
                <Reveal delay={0.2}>
                    <div className="mt-6 flex flex-col items-center justify-between gap-4 rounded-2xl border border-white/10 bg-white/[0.02] p-6 sm:flex-row sm:p-8">
                        <div className="flex items-start gap-4">
                            <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-white/10 bg-violet-500/10 text-violet-300">
                                <Building2 className="h-5 w-5" />
                            </span>
                            <div>
                                <h3 className="font-display text-lg font-semibold text-white">
                                    {t('landing.pricing.enterprise')}
                                </h3>
                                <p className="mt-1 max-w-xl text-sm text-slate-400">
                                    {t('landing.pricing.enterpriseBody')}
                                </p>
                            </div>
                        </div>
                        <a
                            href={SALES_MAILTO}
                            className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl border border-white/15 bg-white/5 px-5 py-2.5 text-sm font-semibold text-slate-100 transition-colors hover:bg-white/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
                        >
                            {t('landing.pricing.contactSales')} <ArrowRight className="h-4 w-4" />
                        </a>
                    </div>
                </Reveal>

                <p className="mt-6 text-center text-xs text-slate-500">{t('landing.pricing.note')}</p>
            </Container>
        </Section>
    );
}
