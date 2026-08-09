import { Check, X } from 'lucide-react';
import { Container, Reveal, Section, SectionHeading } from './primitives';
import { useI18n } from '../../context/I18nContext';

export default function ComparisonSection() {
    const { locale } = useI18n();
    const isEs = locale === 'es';
    const withoutItems = isEs
        ? [
              'Actualizar marketplaces por separado',
              'Revisar proveedores manualmente',
              'Depender de múltiples hojas de cálculo',
              'Detectar problemas de stock después de recibir pedidos',
              'Visibilidad limitada de la actividad del equipo',
          ]
        : [
              'Update marketplaces separately',
              'Check suppliers manually',
              'Depend on multiple spreadsheets',
              'Discover stock issues after orders arrive',
              'Limited visibility into team activity',
          ];
    const withItems = isEs
        ? [
              'Controlar tiendas desde un panel único',
              'Monitorear precios y stock de proveedores',
              'Aplicar reglas de automatización reutilizables',
              'Ejecutar actualizaciones con workers en segundo plano',
              'Revisar logs, analíticas e historial de auditoría',
          ]
        : [
              'Control stores from one dashboard',
              'Monitor vendor prices and stock',
              'Apply reusable automation rules',
              'Run updates through background workers',
              'Review logs, analytics, and audit history',
          ];

    return (
        <Section>
            <Container>
                <Reveal>
                    <SectionHeading
                        eyebrow={isEs ? 'Antes y después' : 'Before & after'}
                        title={
                            isEs
                                ? 'De hojas dispersas a un flujo controlado.'
                                : 'From scattered spreadsheets to one controlled workflow.'
                        }
                    />
                </Reveal>

                <div className="mt-12 grid gap-5 lg:grid-cols-2">
                    <Reveal>
                        <div className="h-full rounded-2xl border border-white/10 bg-white/[0.02] p-6 sm:p-8">
                            <h3 className="font-display text-lg font-semibold text-slate-300">
                                {isEs ? 'Sin SellerPilot Hub' : 'Without SellerPilot Hub'}
                            </h3>
                            <ul className="mt-5 space-y-3">
                                {withoutItems.map((item) => (
                                    <li key={item} className="flex items-start gap-3 text-sm text-slate-400">
                                        <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-red-400/10 text-red-300">
                                            <X className="h-3 w-3" />
                                        </span>
                                        {item}
                                    </li>
                                ))}
                            </ul>
                        </div>
                    </Reveal>

                    <Reveal delay={0.1}>
                        <div className="relative h-full overflow-hidden rounded-2xl border border-sky-400/30 bg-gradient-to-b from-sky-500/[0.08] to-transparent p-6 sm:p-8">
                            <div className="pointer-events-none absolute -right-20 -top-20 h-48 w-48 rounded-full bg-sky-500/10 blur-3xl" />
                            <h3 className="font-display text-lg font-semibold text-white">
                                {isEs ? 'Con SellerPilot Hub' : 'With SellerPilot Hub'}
                            </h3>
                            <ul className="mt-5 space-y-3">
                                {withItems.map((item) => (
                                    <li key={item} className="flex items-start gap-3 text-sm text-slate-200">
                                        <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-emerald-400/15 text-emerald-300">
                                            <Check className="h-3 w-3" />
                                        </span>
                                        {item}
                                    </li>
                                ))}
                            </ul>
                        </div>
                    </Reveal>
                </div>
            </Container>
        </Section>
    );
}
