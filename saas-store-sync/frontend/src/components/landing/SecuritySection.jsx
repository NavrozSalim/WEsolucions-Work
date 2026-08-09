import * as Icons from 'lucide-react';
import { Container, Reveal, Section, SectionHeading } from './primitives';
import { useI18n } from '../../context/I18nContext';

export default function SecuritySection() {
    const { t, locale } = useI18n();
    const isEs = locale === 'es';
    const points = [
        {
            icon: 'Lock',
            title: isEs ? 'Credenciales cifradas' : 'Encrypted credentials',
            body: isEs
                ? 'Los tokens de marketplaces se cifran en reposo con una clave Fernet.'
                : 'Marketplace API tokens are encrypted at rest with a Fernet key.',
        },
        {
            icon: 'Building2',
            title: isEs ? 'Acceso por organización' : 'Organization-based access',
            body: isEs
                ? 'Los miembros pertenecen a una organización y comparten solo sus datos de tienda.'
                : 'Members belong to an organization and share only its store data.',
        },
        {
            icon: 'ShieldCheck',
            title: isEs ? 'Permisos por módulo' : 'Module permissions',
            body: isEs
                ? 'Super Users asignan acceso por módulo: dashboard, stores, catalog y más.'
                : 'Super Users grant access per module — dashboard, stores, catalog, and more.',
        },
        {
            icon: 'ScrollText',
            title: isEs ? 'Visibilidad de auditoría' : 'Audit visibility',
            body: isEs
                ? 'Las acciones clave se registran para revisar quién cambió qué.'
                : 'Key actions are recorded so you can review who changed what.',
        },
        {
            icon: 'Server',
            title: isEs ? 'Procesamiento en segundo plano' : 'Background job processing',
            body: isEs
                ? 'El trabajo pesado corre en workers de Celery, fuera del request principal.'
                : 'Heavy work runs on Celery workers, isolated from the request path.',
        },
        {
            icon: 'HeartPulse',
            title: isEs ? 'Health y readiness' : 'Health & readiness',
            body: isEs
                ? 'Endpoints de health/readiness ayudan a monitoreo y orquestación.'
                : 'Health and readiness endpoints support orchestration and monitoring.',
        },
    ];

    return (
        <Section>
            <Container>
                <Reveal>
                    <SectionHeading
                        eyebrow={t('landing.security.eyebrow')}
                        title={t('landing.security.title')}
                        subtitle={t('landing.security.subtitle')}
                    />
                </Reveal>

                <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                    {points.map((p, i) => {
                        const Icon = Icons[p.icon] || Icons.Shield;
                        return (
                            <Reveal key={p.title} delay={(i % 3) * 0.06}>
                                <div className="h-full rounded-2xl border border-white/10 bg-white/[0.02] p-6">
                                    <span className="grid h-11 w-11 place-items-center rounded-xl border border-white/10 bg-emerald-400/10 text-emerald-300">
                                        <Icon className="h-5 w-5" />
                                    </span>
                                    <h3 className="mt-5 font-display text-base font-semibold text-white">{p.title}</h3>
                                    <p className="mt-2 text-sm leading-relaxed text-slate-400">{p.body}</p>
                                </div>
                            </Reveal>
                        );
                    })}
                </div>
            </Container>
        </Section>
    );
}
