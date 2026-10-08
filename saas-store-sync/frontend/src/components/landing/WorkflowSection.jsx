import { useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import * as Icons from 'lucide-react';
import { Container, Reveal, Section, SectionHeading } from './primitives';
import { useI18n } from '../../context/I18nContext';

const STEP_ICONS = ['Store', 'Link2', 'SlidersHorizontal', 'RefreshCw'];

function StepButton({ step, index, active, onSelect }) {
    const Icon = Icons[step.icon] || Icons.Circle;
    return (
        <button
            type="button"
            onClick={onSelect}
            aria-pressed={active}
            className={`relative w-full rounded-2xl border p-5 text-left transition-colors focus:outline-hidden focus-visible:ring-2 focus-visible:ring-sky-400 ${
                active ? 'border-sky-400/40 bg-sky-400/10' : 'border-white/10 bg-[#0b1120] hover:border-white/20'
            }`}
        >
            <div className="flex items-center justify-between">
                <span className="grid h-11 w-11 place-items-center rounded-xl border border-white/10 bg-white/3 text-sky-300">
                    <Icon className="h-5 w-5" />
                </span>
                <span className="rounded-full border border-white/10 px-2.5 py-1 text-[11px] font-semibold tracking-wide text-slate-400">
                    #{index + 1}
                </span>
            </div>
            <h3 className="mt-4 font-display text-base font-semibold text-white">{step.title}</h3>
            <p className="mt-2 text-sm leading-relaxed text-slate-400">{step.body}</p>
        </button>
    );
}

function StagePanel({ index, margin, onMargin }) {
    const { t } = useI18n();
    const supplier = 18.4;
    const storePrice = (supplier * (1 + margin / 100)).toFixed(2);

    if (index === 0) {
        return (
            <div className="rounded-xl border border-emerald-400/20 bg-emerald-400/5 p-4">
                <div className="flex items-center justify-between">
                    <div>
                        <p className="text-sm font-medium text-white">Bunnings</p>
                        <p className="text-xs text-slate-400">bunnings-prod.mirakl.net</p>
                    </div>
                    <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-400/30 bg-emerald-400/10 px-2.5 py-1 text-[11px] font-medium text-emerald-300">
                        <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 spl-pulse-dot" />
                        Connected
                    </span>
                </div>
            </div>
        );
    }

    if (index === 1) {
        return (
            <div className="space-y-2">
                {[
                    ['Amazon US', 'Bunnings · SKU-1044'],
                    ['eBay AU', 'Reverb · SKU-2281'],
                    ['Vevor', 'Temu · SKU-7730'],
                ].map(([from, to]) => (
                    <div key={from} className="flex items-center justify-between gap-3 rounded-xl border border-white/10 bg-slate-950/40 px-3 py-3 text-sm">
                        <span className="text-slate-300">{from}</span>
                        <Icons.ArrowRight className="h-4 w-4 shrink-0 text-sky-300" />
                        <span className="font-medium text-white">{to}</span>
                    </div>
                ))}
            </div>
        );
    }

    if (index === 2) {
        return (
            <div className="rounded-xl border border-white/10 bg-slate-950/40 p-4">
                <div className="flex items-center justify-between text-sm">
                    <span className="text-slate-300">{t('landing.workflow.margin')}</span>
                    <span className="font-semibold text-sky-200">{margin}%</span>
                </div>
                <input
                    type="range"
                    min="10"
                    max="120"
                    value={margin}
                    onChange={(event) => onMargin(Number(event.target.value))}
                    onWheel={(event) => event.currentTarget.blur()}
                    className="mt-4 w-full accent-sky-400"
                    aria-label={t('landing.workflow.margin')}
                />
                <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
                    <div className="rounded-lg border border-white/10 px-3 py-2">
                        <p className="text-[11px] text-slate-500">{t('landing.workflow.supplierPrice')}</p>
                        <p className="font-medium text-white">${supplier.toFixed(2)}</p>
                    </div>
                    <div className="rounded-lg border border-sky-400/30 bg-sky-400/10 px-3 py-2">
                        <p className="text-[11px] text-sky-200/80">{t('landing.workflow.storePrice')}</p>
                        <p className="font-medium text-white">${storePrice}</p>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-2">
            {[
                ['Supplier scrape · Amazon', 'done'],
                ['Pricing rule · Bunnings', 'done'],
                ['Inventory pushed · Bunnings', 'running'],
            ].map(([label, state]) => (
                <div key={label} className="flex items-center justify-between rounded-xl border border-white/10 px-3 py-3 text-sm">
                    <span className="flex items-center gap-2 text-slate-200">
                        {state === 'running' ? (
                            <Icons.Loader2 className="h-4 w-4 animate-spin text-sky-300" />
                        ) : (
                            <Icons.CheckCircle2 className="h-4 w-4 text-emerald-300" />
                        )}
                        {label}
                    </span>
                    <span className="text-xs capitalize text-slate-500">{state}</span>
                </div>
            ))}
        </div>
    );
}

export default function WorkflowSection() {
    const { t } = useI18n();
    const reduce = useReducedMotion();
    const [active, setActive] = useState(0);
    const [margin, setMargin] = useState(90);

    const steps = [1, 2, 3, 4].map((n, index) => ({
        icon: STEP_ICONS[index],
        title: t(`landing.workflow.step${n}Title`),
        body: t(`landing.workflow.step${n}Body`),
    }));

    return (
        <Section id="how-it-works">
            <Container>
                <Reveal>
                    <SectionHeading eyebrow={t('landing.workflow.eyebrow')} title={t('landing.workflow.title')} />
                </Reveal>

                <div className="mt-14 grid items-start gap-6 lg:grid-cols-2">
                    <div className="grid gap-3">
                        {steps.map((step, index) => (
                            <StepButton
                                key={step.title}
                                step={step}
                                index={index}
                                active={active === index}
                                onSelect={() => setActive(index)}
                            />
                        ))}
                    </div>

                    <div className="rounded-2xl border border-white/10 bg-[#0b1120] p-5 sm:p-6">
                        <p className="text-[11px] font-medium uppercase tracking-wide text-sky-300">
                            #{active + 1} · {steps[active].title}
                        </p>
                        <p className="mt-2 text-sm leading-relaxed text-slate-400">{steps[active].body}</p>
                        <div className="mt-5">
                            <AnimatePresence mode="wait">
                                <motion.div
                                    key={active}
                                    initial={reduce ? false : { opacity: 0, y: 10 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    exit={reduce ? {} : { opacity: 0, y: -8 }}
                                    transition={{ duration: 0.25 }}
                                >
                                    <StagePanel index={active} margin={margin} onMargin={setMargin} />
                                </motion.div>
                            </AnimatePresence>
                        </div>
                    </div>
                </div>
            </Container>
        </Section>
    );
}
