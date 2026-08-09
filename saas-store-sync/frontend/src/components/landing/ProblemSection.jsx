import { ArrowRight, Network, PackageX, TrendingUp } from 'lucide-react';
import { Container, Reveal, Section, SectionHeading } from './primitives';
import { useI18n } from '../../context/I18nContext';

export default function ProblemSection() {
    const { t } = useI18n();

    const problems = [
        { icon: TrendingUp, title: t('landing.problem.card1Title'), body: t('landing.problem.card1Body') },
        { icon: PackageX, title: t('landing.problem.card2Title'), body: t('landing.problem.card2Body') },
        { icon: Network, title: t('landing.problem.card3Title'), body: t('landing.problem.card3Body') },
    ];

    return (
        <Section>
            <Container>
                <Reveal>
                    <SectionHeading eyebrow={t('landing.problem.eyebrow')} title={t('landing.problem.title')} />
                </Reveal>

                <div className="mt-12 grid gap-5 md:grid-cols-3">
                    {problems.map((p, i) => {
                        const Icon = p.icon;
                        return (
                            <Reveal key={p.title} delay={i * 0.08}>
                                <div className="h-full rounded-2xl border border-white/10 bg-white/[0.02] p-6 transition-colors hover:border-white/20">
                                    <span className="grid h-11 w-11 place-items-center rounded-xl border border-red-400/20 bg-red-400/10 text-red-300">
                                        <Icon className="h-5 w-5" />
                                    </span>
                                    <h3 className="mt-5 font-display text-lg font-semibold text-white">{p.title}</h3>
                                    <p className="mt-2 text-sm leading-relaxed text-slate-400">{p.body}</p>
                                </div>
                            </Reveal>
                        );
                    })}
                </div>

                <Reveal delay={0.25}>
                    <div className="mt-10 flex items-center justify-center">
                        <p className="inline-flex items-center gap-2 rounded-full border border-sky-400/25 bg-sky-400/5 px-5 py-2.5 text-sm font-medium text-slate-200">
                            <ArrowRight className="h-4 w-4 text-sky-300" />
                            {t('landing.problem.closing')}
                        </p>
                    </div>
                </Reveal>
            </Container>
        </Section>
    );
}
