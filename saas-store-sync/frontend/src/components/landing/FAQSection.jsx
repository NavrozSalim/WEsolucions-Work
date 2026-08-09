import { useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { ChevronDown } from 'lucide-react';
import { Container, Reveal, Section, SectionHeading } from './primitives';
import { useI18n } from '../../context/I18nContext';

function FAQItem({ item, index, open, onToggle }) {
    const reduce = useReducedMotion();
    const panelId = `spl-faq-panel-${index}`;
    const buttonId = `spl-faq-button-${index}`;
    return (
        <div className="rounded-2xl border border-white/10 bg-white/[0.02]">
            <h3>
                <button
                    id={buttonId}
                    type="button"
                    aria-expanded={open}
                    aria-controls={panelId}
                    onClick={onToggle}
                    className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 focus-visible:ring-offset-2 focus-visible:ring-offset-[#060910] rounded-2xl"
                >
                    <span className="text-sm font-medium text-white sm:text-base">{item.q}</span>
                    <ChevronDown
                        className={`h-5 w-5 shrink-0 text-slate-400 transition-transform duration-300 ${open ? 'rotate-180' : ''}`}
                    />
                </button>
            </h3>
            <AnimatePresence initial={false}>
                {open ? (
                    <motion.div
                        id={panelId}
                        role="region"
                        aria-labelledby={buttonId}
                        initial={reduce ? false : { height: 0, opacity: 0 }}
                        animate={{ height: 'auto', opacity: 1 }}
                        exit={reduce ? {} : { height: 0, opacity: 0 }}
                        transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
                        className="overflow-hidden"
                    >
                        <p className="px-5 pb-5 text-sm leading-relaxed text-slate-400">{item.a}</p>
                    </motion.div>
                ) : null}
            </AnimatePresence>
        </div>
    );
}

export default function FAQSection() {
    const { t, locale } = useI18n();
    const isEs = locale === 'es';
    const [openIndex, setOpenIndex] = useState(0);
    const faqs = [
        {
            q: isEs ? '¿Qué marketplaces puedo conectar?' : 'Which marketplaces can I connect?',
            a: isEs
                ? 'Puedes conectar Walmart, Reverb, Sears y Etsy.'
                : 'You can connect Walmart, Reverb, Sears, and Etsy.',
        },
        {
            q: isEs ? '¿Cómo funciona el mapeo de productos?' : 'How does product mapping work?',
            a: isEs
                ? 'Mapeas cada fila del catálogo marketplace con su URL correcta de proveedor para monitorear precio y stock.'
                : 'You map each marketplace catalog row to its correct supplier product URL. SellerPilot Hub then monitors that supplier source for price and stock changes.',
        },
        {
            q: isEs
                ? '¿SellerPilot Hub puede actualizar precios automáticamente?'
                : 'Can SellerPilot Hub update prices automatically?',
            a: isEs
                ? 'Sí. Configuras reglas por tienda y los workers aplican y publican actualizaciones.'
                : 'Yes. You configure pricing rules per store, and background jobs apply them and push updates to your marketplaces.',
        },
        {
            q: isEs ? '¿Puede prevenir sobreventa?' : 'Can it prevent overselling?',
            a: isEs
                ? 'La automatización de inventario reduce el riesgo de sobreventa usando reglas y sincronización periódica.'
                : 'Inventory automation applies stock rules and syncs availability, which reduces the risk of overselling.',
        },
        {
            q: isEs ? '¿Puedo invitar miembros del equipo?' : 'Can I invite team members?',
            a: isEs
                ? 'Sí. Un Super User crea cuentas y asigna permisos por módulo.'
                : 'Yes. A Super User creates user accounts and assigns per-module permissions.',
        },
        {
            q: isEs ? '¿Cómo se protegen las credenciales?' : 'How are marketplace credentials protected?',
            a: isEs
                ? 'Se cifran en reposo y el acceso está limitado por organización y permisos.'
                : 'Credentials are encrypted at rest using a Fernet key, and access is scoped to your organization with module-level permissions.',
        },
        {
            q: isEs ? '¿Necesito conocimientos técnicos?' : 'Do I need technical knowledge?',
            a: isEs
                ? 'No. Puedes conectar tiendas y configurar reglas desde la interfaz.'
                : 'No. You connect stores, map products, and configure rules through the dashboard.',
        },
        {
            q: isEs ? '¿Qué pasa cuando falla una sincronización?' : 'What happens when a sync fails?',
            a: isEs
                ? 'El fallo aparece en actividad y logs para que puedas revisarlo y reintentar.'
                : 'Failed jobs appear in sync visibility with status, logs, and activity so you can investigate and re-run them.',
        },
    ];

    return (
        <Section id="faq">
            <Container>
                <Reveal>
                    <SectionHeading eyebrow={t('landing.faq.eyebrow')} title={t('landing.faq.title')} />
                </Reveal>

                <div className="mx-auto mt-12 grid max-w-3xl gap-3">
                    {faqs.map((item, i) => (
                        <Reveal key={item.q} delay={i * 0.03}>
                            <FAQItem
                                item={item}
                                index={i}
                                open={openIndex === i}
                                onToggle={() => setOpenIndex((cur) => (cur === i ? -1 : i))}
                            />
                        </Reveal>
                    ))}
                </div>
            </Container>
        </Section>
    );
}
