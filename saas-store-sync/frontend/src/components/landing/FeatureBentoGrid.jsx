import * as Icons from 'lucide-react';
import { Container, Reveal, Section, SectionHeading } from './primitives';
import { useI18n } from '../../context/I18nContext';

export default function FeatureBentoGrid() {
    const { t, locale } = useI18n();
    const isEs = locale === 'es';

    const features = [
        {
            icon: 'LayoutDashboard',
            title: isEs ? 'Panel multi-tienda' : 'Multi-Store Dashboard',
            body: isEs
                ? 'Gestiona tiendas de marketplace y actividad operativa desde un solo panel.'
                : 'Manage marketplace stores and operational activity from one dashboard.',
            span: 'lg:col-span-2',
        },
        {
            icon: 'LineChart',
            title: isEs ? 'Inteligencia de precios de proveedores' : 'Vendor Price Intelligence',
            body: isEs
                ? 'Monitorea precios de proveedores e identifica cambios de costo.'
                : 'Monitor supplier pricing and identify product cost changes.',
        },
        {
            icon: 'PackageCheck',
            title: isEs ? 'Automatización de inventario' : 'Inventory Automation',
            body: isEs
                ? 'Aplica reglas de stock y reduce el riesgo de sobreventa.'
                : 'Apply stock rules and reduce the risk of overselling.',
        },
        {
            icon: 'Link2',
            title: isEs ? 'Mapeo de productos' : 'Product Mapping',
            body: isEs
                ? 'Conecta filas del catálogo marketplace con sus URL correctas de proveedor.'
                : 'Connect marketplace catalog rows to their correct supplier product URLs.',
        },
        {
            icon: 'RefreshCw',
            title: isEs ? 'Sincronizaciones automáticas' : 'Automated Sync Jobs',
            body: isEs
                ? 'Ejecuta scraping, ingestión, precios, inventario y actualizaciones en segundo plano.'
                : 'Run scraping, ingestion, pricing, inventory, and marketplace updates in the background.',
            span: 'lg:col-span-2',
        },
        {
            icon: 'Users',
            title: isEs ? 'Permisos de equipo' : 'Team Permissions',
            body: isEs
                ? 'Crea cuentas y controla acceso a dashboard, stores, catalog, orders, tickets y team.'
                : 'Create user accounts and control access to dashboard, stores, catalog, orders, tickets, and team.',
        },
        {
            icon: 'Activity',
            title: isEs ? 'Visibilidad de sync' : 'Sync Visibility',
            body: isEs
                ? 'Revisa estados de jobs, fallos, logs, actividad e historial de auditoría.'
                : 'Review job status, failures, logs, activity, and audit history.',
        },
        {
            icon: 'FileSpreadsheet',
            title: isEs ? 'Flujos masivos de catálogo' : 'Bulk Catalog Workflows',
            body: isEs
                ? 'Importa y gestiona grandes catálogos con CSV y hojas de cálculo.'
                : 'Import and manage large product catalogs through CSV and spreadsheet workflows.',
        },
    ];

    return (
        <Section id="product">
            <Container>
                <Reveal>
                    <SectionHeading
                        eyebrow={t('landing.features.eyebrow')}
                        title={t('landing.features.title')}
                        subtitle={t('landing.features.subtitle')}
                    />
                </Reveal>

                <div className="mt-12 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                    {features.map((f, i) => {
                        const Icon = Icons[f.icon] || Icons.Sparkles;
                        return (
                            <Reveal key={f.title} delay={(i % 3) * 0.06} className={f.span || ''}>
                                <div className="group relative h-full overflow-hidden rounded-2xl border border-white/10 bg-white/[0.02] p-6 transition-colors hover:border-sky-400/30">
                                    <div className="pointer-events-none absolute -right-16 -top-16 h-40 w-40 rounded-full bg-sky-500/5 blur-2xl transition-opacity duration-300 group-hover:opacity-100 opacity-0" />
                                    <span className="grid h-11 w-11 place-items-center rounded-xl border border-white/10 bg-gradient-to-br from-sky-400/15 to-cyan-400/10 text-sky-300">
                                        <Icon className="h-5 w-5" />
                                    </span>
                                    <h3 className="mt-5 font-display text-lg font-semibold text-white">{f.title}</h3>
                                    <p className="mt-2 max-w-md text-sm leading-relaxed text-slate-400">{f.body}</p>
                                </div>
                            </Reveal>
                        );
                    })}
                </div>
            </Container>
        </Section>
    );
}
