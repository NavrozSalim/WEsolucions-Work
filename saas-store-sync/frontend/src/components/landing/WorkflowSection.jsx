import * as Icons from 'lucide-react';
import { Container, Reveal, Section, SectionHeading } from './primitives';
import { useI18n } from '../../context/I18nContext';

function StepCard({ step, index }) {
    const Icon = Icons[step.icon] || Icons.Circle;
    return (
        <div className="relative z-10 h-full rounded-2xl border border-white/10 bg-[#0b1120] p-6">
            <div className="flex items-center justify-between">
                <span className="grid h-11 w-11 place-items-center rounded-xl border border-white/10 bg-gradient-to-br from-sky-400/15 to-cyan-400/10 text-sky-300">
                    <Icon className="h-5 w-5" />
                </span>
                <span className="font-display text-3xl font-semibold text-white/10">
                    {String(index + 1).padStart(2, '0')}
                </span>
            </div>
            <h3 className="mt-5 font-display text-base font-semibold text-white">{step.title}</h3>
            <p className="mt-2 text-sm leading-relaxed text-slate-400">{step.body}</p>
        </div>
    );
}

export default function WorkflowSection() {
    const { t } = useI18n();

    const steps = [
        {
            icon: 'Store',
            title: t('landing.workflow.step1Title'),
            body: t('landing.workflow.step1Body'),
        },
        {
            icon: 'Link2',
            title: t('landing.workflow.step2Title'),
            body: t('landing.workflow.step2Body'),
        },
        {
            icon: 'SlidersHorizontal',
            title: t('landing.workflow.step3Title'),
            body: t('landing.workflow.step3Body'),
        },
        {
            icon: 'RefreshCw',
            title: t('landing.workflow.step4Title'),
            body: t('landing.workflow.step4Body'),
        },
    ];

    return (
        <Section id="how-it-works">
            <Container>
                <Reveal>
                    <SectionHeading
                        eyebrow={t('landing.workflow.eyebrow')}
                        title={t('landing.workflow.title')}
                    />
                </Reveal>

                <div className="mt-14 grid gap-5 lg:grid-cols-4">
                    {steps.map((step, i) => (
                        <Reveal key={step.title} delay={i * 0.1} className="relative">
                            <StepCard step={step} index={i} />
                        </Reveal>
                    ))}
                </div>
            </Container>
        </Section>
    );
}
