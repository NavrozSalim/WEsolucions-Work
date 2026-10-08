import { useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { Container, GridBackground, Reveal, Section, SectionHeading } from './primitives';
import { INTEGRATIONS } from './landingContent';
import { useI18n } from '../../context/I18nContext';

const TAG_TONE = {
    publish: 'border-emerald-400/30 bg-emerald-400/10 text-emerald-300',
    catalog: 'border-sky-400/30 bg-sky-400/10 text-sky-300',
    sheet: 'border-amber-400/30 bg-amber-400/10 text-amber-300',
    source: 'border-violet-400/30 bg-violet-400/10 text-violet-300',
};

function tickerLoop(items) {
    const half = [];
    while (half.length < 14) half.push(...items);
    return [...half, ...half];
}

function Ticker({ items, selectedName, tagLabel, onSelect, reverse = false }) {
    const loop = tickerLoop(items);
    return (
        <div className="spl-marquee-track relative overflow-hidden py-1 mask-[linear-gradient(to_right,transparent,#000_6%,#000_94%,transparent)]">
            <div className={`spl-marquee ${reverse ? 'spl-marquee-reverse' : ''}`} style={{ animationDuration: '42s' }}>
                {loop.map((item, index) => {
                    const selected = selectedName === item.name;
                    return (
                        <button
                            key={`${item.name}-${index}`}
                            type="button"
                            onClick={() => onSelect(item.name)}
                            aria-pressed={selected}
                            className={`mx-2 inline-flex items-center gap-2 rounded-full border px-4 py-2 transition-colors focus:outline-hidden focus-visible:ring-2 focus-visible:ring-sky-400 ${
                                selected ? 'border-sky-400/60 bg-sky-400/15' : 'border-white/10 bg-white/4 hover:border-white/25'
                            }`}
                        >
                            <span className="font-display text-sm font-semibold text-slate-100">{item.name}</span>
                            <span className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${TAG_TONE[item.tag]}`}>
                                {tagLabel(item.tag)}
                            </span>
                        </button>
                    );
                })}
            </div>
        </div>
    );
}

export default function IntegrationLogos() {
    const { t } = useI18n();
    const reduce = useReducedMotion();
    const [selectedName, setSelectedName] = useState(INTEGRATIONS[0].name);
    const selected = INTEGRATIONS.find((item) => item.name === selectedName) || INTEGRATIONS[0];
    const stores = INTEGRATIONS.filter((item) => item.group === 'store');
    const suppliers = INTEGRATIONS.filter((item) => item.group === 'supplier');
    const tagLabel = (tag) => t(`landing.integrations.${tag}`);

    return (
        <Section id="integrations">
            <GridBackground />
            <Container className="relative">
                <Reveal>
                    <SectionHeading
                        eyebrow={t('landing.integrations.eyebrow')}
                        title={t('landing.integrations.title')}
                        subtitle={t('landing.integrations.subtitle')}
                    />
                </Reveal>
            </Container>

            <div className="relative mt-12 space-y-6">
                <div>
                    <p className="mb-3 text-center text-[11px] font-medium uppercase tracking-wide text-slate-400">
                        {t('landing.integrations.sellOn')}
                    </p>
                    {reduce ? (
                        <Container>
                            <div className="flex flex-wrap justify-center gap-2">
                                {stores.map((item) => (
                                    <button
                                        key={item.name}
                                        type="button"
                                        onClick={() => setSelectedName(item.name)}
                                        className="rounded-full border border-white/10 bg-white/4 px-4 py-2 text-sm font-semibold text-slate-100"
                                    >
                                        {item.name}
                                    </button>
                                ))}
                            </div>
                        </Container>
                    ) : (
                        <Ticker items={stores} selectedName={selectedName} tagLabel={tagLabel} onSelect={setSelectedName} />
                    )}
                </div>
                <div>
                    <p className="mb-3 text-center text-[11px] font-medium uppercase tracking-wide text-slate-400">
                        {t('landing.integrations.sourceFrom')}
                    </p>
                    {reduce ? (
                        <Container>
                            <div className="flex flex-wrap justify-center gap-2">
                                {suppliers.map((item) => (
                                    <button
                                        key={item.name}
                                        type="button"
                                        onClick={() => setSelectedName(item.name)}
                                        className="rounded-full border border-white/10 bg-white/4 px-4 py-2 text-sm font-semibold text-slate-100"
                                    >
                                        {item.name}
                                    </button>
                                ))}
                            </div>
                        </Container>
                    ) : (
                        <Ticker
                            items={suppliers}
                            selectedName={selectedName}
                            tagLabel={tagLabel}
                            onSelect={setSelectedName}
                            reverse
                        />
                    )}
                </div>
            </div>

            <Container className="relative">
                <AnimatePresence mode="wait">
                    <motion.div
                        key={selected.name}
                        initial={reduce ? false : { opacity: 0, y: 8 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={reduce ? {} : { opacity: 0, y: -8 }}
                        transition={{ duration: 0.22 }}
                        className="mx-auto mt-8 max-w-xl rounded-2xl border border-white/10 bg-[#0b1120] px-6 py-5 text-center"
                    >
                        <span className={`inline-flex rounded-full border px-2.5 py-1 text-[11px] font-medium ${TAG_TONE[selected.tag]}`}>
                            {tagLabel(selected.tag)}
                        </span>
                        <h3 className="mt-3 font-display text-2xl font-semibold text-white">{selected.name}</h3>
                        <p className="mt-2 text-sm leading-relaxed text-slate-400">{selected.detail}</p>
                    </motion.div>
                </AnimatePresence>
            </Container>
        </Section>
    );
}
