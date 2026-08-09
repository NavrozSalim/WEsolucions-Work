import { Container, Reveal, Section, SectionHeading } from './primitives';
import { INTEGRATIONS } from './landingContent';
import { useI18n } from '../../context/I18nContext';

function IntegrationBadge({ name, status, labels }) {
    const styles = {
        available: {
            label: labels.available,
            className: 'border-emerald-400/30 bg-emerald-400/10 text-emerald-300',
            dot: 'bg-emerald-400',
        },
        beta: {
            label: labels.beta,
            className: 'border-amber-400/30 bg-amber-400/10 text-amber-300',
            dot: 'bg-amber-400',
        },
        planned: {
            label: labels.planned,
            className: 'border-slate-400/30 bg-slate-400/10 text-slate-300',
            dot: 'bg-slate-400',
        },
    };
    const s = styles[status];
    return (
        <div className="mx-3 flex items-center gap-2.5 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-2.5">
            <span className="font-display text-sm font-semibold text-slate-100">{name}</span>
            <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium ${s.className}`}>
                <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />
                {s.label}
            </span>
        </div>
    );
}

export default function IntegrationLogos() {
    const { t } = useI18n();
    const labels = {
        available: t('landing.integrations.available'),
        beta: t('landing.integrations.beta'),
        planned: t('landing.integrations.planned'),
    };
    const loop = [...INTEGRATIONS, ...INTEGRATIONS];

    return (
        <Section id="integrations">
            <Container>
                <Reveal>
                    <SectionHeading
                        eyebrow={t('landing.integrations.eyebrow')}
                        title={t('landing.integrations.title')}
                        subtitle={t('landing.integrations.subtitle')}
                    />
                </Reveal>
            </Container>

            <Reveal delay={0.1}>
                <div className="spl-marquee-track relative mt-12 overflow-hidden [mask-image:linear-gradient(to_right,transparent,#000_8%,#000_92%,transparent)]">
                    <div className="spl-marquee py-1">
                        {loop.map((it, i) => (
                            <IntegrationBadge key={`${it.name}-${i}`} name={it.name} status={it.status} labels={labels} />
                        ))}
                    </div>
                </div>
            </Reveal>

            <Container>
                <Reveal delay={0.15}>
                    <div className="mt-8 flex flex-wrap items-center justify-center gap-4 text-xs text-slate-500">
                        {Object.entries(labels).map(([key, label]) => (
                            <span key={key} className="inline-flex items-center gap-1.5">
                                <span
                                    className={`h-2 w-2 rounded-full ${
                                        key === 'available'
                                            ? 'bg-emerald-400'
                                            : key === 'beta'
                                              ? 'bg-amber-400'
                                              : 'bg-slate-400'
                                    }`}
                                />
                                {label}
                            </span>
                        ))}
                    </div>
                </Reveal>
            </Container>
        </Section>
    );
}
