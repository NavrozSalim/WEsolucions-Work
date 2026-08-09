import { useState } from 'react';
import { Check, Share2, ShieldCheck, UserPlus, Users } from 'lucide-react';
import { Container, Reveal, Section, SectionHeading } from './primitives';
import { PERMISSION_MODULES } from './landingContent';
import { useI18n } from '../../context/I18nContext';

function PermissionToggle({ label, enabled, onToggle }) {
    return (
        <button
            type="button"
            role="switch"
            aria-checked={enabled}
            onClick={onToggle}
            className="flex w-full items-center justify-between rounded-xl border border-white/10 bg-white/[0.02] px-4 py-3 text-left transition-colors hover:border-white/20 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
        >
            <span className="text-sm font-medium text-slate-200">{label}</span>
            <span
                className={`grid h-5 w-5 place-items-center rounded-md border transition-colors ${
                    enabled ? 'border-sky-400 bg-sky-500 text-white' : 'border-white/20 bg-transparent text-transparent'
                }`}
            >
                <Check className="h-3.5 w-3.5" />
            </span>
        </button>
    );
}

export default function TeamPermissionsSection() {
    const { t, locale } = useI18n();
    const isEs = locale === 'es';
    const [modules, setModules] = useState(PERMISSION_MODULES);
    const capabilities = [
        { icon: Users, text: t('landing.team.cap1') },
        { icon: UserPlus, text: t('landing.team.cap2') },
        { icon: ShieldCheck, text: t('landing.team.cap3') },
        { icon: Share2, text: t('landing.team.cap4') },
    ];

    const toggle = (key) =>
        setModules((prev) => prev.map((m) => (m.key === key ? { ...m, enabled: !m.enabled } : m)));
    const moduleLabel = (key, fallback) => {
        const labelsEs = {
            dashboard: 'Dashboard',
            stores: 'Tiendas',
            catalog: 'Catálogo',
            orders: 'Pedidos',
            tickets: 'Tickets',
            team: 'Equipo',
        };
        if (locale === 'es') return labelsEs[key] || fallback;
        return fallback;
    };

    return (
        <Section>
            <Container>
                <div className="grid items-center gap-12 lg:grid-cols-2">
                    <Reveal>
                        <div>
                            <SectionHeading
                                eyebrow={t('landing.team.eyebrow')}
                                title={t('landing.team.title')}
                                align="left"
                            />
                            <ul className="mt-8 space-y-4">
                                {capabilities.map((c) => {
                                    const Icon = c.icon;
                                    return (
                                        <li key={c.text} className="flex items-start gap-3">
                                            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-white/10 bg-sky-400/10 text-sky-300">
                                                <Icon className="h-4 w-4" />
                                            </span>
                                            <span className="pt-1.5 text-sm text-slate-300">{c.text}</span>
                                        </li>
                                    );
                                })}
                            </ul>
                        </div>
                    </Reveal>

                    <Reveal delay={0.1}>
                        <div className="rounded-2xl border border-white/10 bg-[#121a2b]/90 p-6 shadow-2xl">
                            <div className="flex items-center justify-between">
                                <div className="flex items-center gap-3">
                                    <span className="grid h-10 w-10 place-items-center rounded-full bg-gradient-to-br from-sky-400/30 to-cyan-400/20 text-xs font-semibold text-sky-200">
                                        PS
                                    </span>
                                    <div>
                                        <div className="text-sm font-semibold text-white">Priya Shah</div>
                                        <div className="text-xs text-slate-500">
                                            {isEs ? 'Responsable de catálogo' : 'Catalog manager'}
                                        </div>
                                    </div>
                                </div>
                                <span className="rounded-full border border-white/10 bg-white/5 px-2.5 py-1 text-[11px] text-slate-400">
                                    {t('landing.team.moduleAccess')}
                                </span>
                            </div>

                            <div className="mt-5 grid gap-2.5 sm:grid-cols-2">
                                {modules.map((m) => (
                                    <PermissionToggle
                                        key={m.key}
                                        label={moduleLabel(m.key, m.label)}
                                        enabled={m.enabled}
                                        onToggle={() => toggle(m.key)}
                                    />
                                ))}
                            </div>
                            <p className="mt-4 text-xs text-slate-500">
                                {t('landing.team.panelHint')}
                            </p>
                        </div>
                    </Reveal>
                </div>
            </Container>
        </Section>
    );
}
