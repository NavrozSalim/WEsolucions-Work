import { ArrowRight, CalendarDays } from 'lucide-react';
import { CTAButton, Container, GridBackground, Reveal, Section } from './primitives';
import { SALES_MAILTO } from './landingContent';
import { useI18n } from '../../context/I18nContext';

export default function FinalCTA() {
    const { t } = useI18n();

    return (
        <Section>
            <Container>
                <Reveal>
                    <div className="relative overflow-hidden rounded-3xl border border-white/10 bg-gradient-to-b from-sky-500/[0.12] to-[#0b1120] px-6 py-16 text-center sm:px-12">
                        <div className="spl-aurora" aria-hidden />
                        <GridBackground />
                        <div className="relative mx-auto max-w-2xl">
                            <h2 className="font-display text-3xl font-semibold tracking-tight text-white sm:text-4xl">
                                {t('landing.finalCta.title')}
                            </h2>
                            <p className="mx-auto mt-4 max-w-xl text-base leading-relaxed text-slate-300">
                                {t('landing.finalCta.body')}
                            </p>
                            <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
                                <CTAButton to="/signup/super">
                                    {t('landing.ctaPrimary')} <ArrowRight className="h-4 w-4" />
                                </CTAButton>
                                <CTAButton variant="secondary" href={SALES_MAILTO}>
                                    <CalendarDays className="h-4 w-4" /> {t('landing.ctaSecondary')}
                                </CTAButton>
                            </div>
                        </div>
                    </div>
                </Reveal>
            </Container>
        </Section>
    );
}
