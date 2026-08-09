import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { Menu, X, Zap } from 'lucide-react';
import LanguageSwitcher from '../layout/LanguageSwitcher';
import { useI18n } from '../../context/I18nContext';
import { CTAButton, Container } from './primitives';

export function BrandMark({ withText = true }) {
    return (
        <Link to="/" className="group inline-flex items-center gap-2.5" aria-label="SellerPilot Hub home">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-sky-400 to-cyan-500 text-slate-950 shadow-[0_6px_20px_-6px_rgba(56,189,248,0.7)]">
                <Zap className="h-5 w-5" strokeWidth={2.5} />
            </span>
            {withText ? (
                <span className="font-display text-base font-semibold tracking-tight text-white">
                    SellerPilot <span className="text-sky-400">Hub</span>
                </span>
            ) : null}
        </Link>
    );
}

export default function Navbar() {
    const { t } = useI18n();
    const [scrolled, setScrolled] = useState(false);
    const [open, setOpen] = useState(false);
    const reduce = useReducedMotion();

    const navLinks = [
        { label: t('nav.product'), href: '#product' },
        { label: t('nav.integrations'), href: '#integrations' },
        { label: t('nav.howItWorks'), href: '#how-it-works' },
        { label: t('nav.faq'), href: '#faq' },
    ];

    useEffect(() => {
        const onScroll = () => setScrolled(window.scrollY > 12);
        onScroll();
        window.addEventListener('scroll', onScroll, { passive: true });
        return () => window.removeEventListener('scroll', onScroll);
    }, []);

    useEffect(() => {
        document.body.style.overflow = open ? 'hidden' : '';
        return () => {
            document.body.style.overflow = '';
        };
    }, [open]);

    return (
        <header className="fixed inset-x-0 top-0 z-50">
            <div
                className={`transition-all duration-300 ${
                    scrolled
                        ? 'border-b border-white/10 bg-[#060910]/80 backdrop-blur-xl'
                        : 'border-b border-transparent bg-transparent'
                }`}
            >
                <Container className="flex h-16 items-center justify-between">
                    <BrandMark />

                    <nav className="hidden items-center gap-1 lg:flex" aria-label="Primary">
                        {navLinks.map((link) => (
                            <a
                                key={link.href}
                                href={link.href}
                                className="rounded-lg px-3 py-2 text-sm font-medium text-slate-300 transition-colors hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
                            >
                                {link.label}
                            </a>
                        ))}
                    </nav>

                    <div className="hidden items-center gap-3 lg:flex">
                        <LanguageSwitcher className="text-slate-200" />
                        <Link
                            to="/login/choose"
                            className="rounded-lg px-3 py-2 text-sm font-semibold text-slate-200 transition-colors hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
                        >
                            {t('nav.login')}
                        </Link>
                        <CTAButton to="/signup/super" className="px-4 py-2">
                            {t('nav.startFree')}
                        </CTAButton>
                    </div>

                    <button
                        type="button"
                        onClick={() => setOpen(true)}
                        className="grid h-10 w-10 place-items-center rounded-lg border border-white/10 text-slate-200 lg:hidden focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
                        aria-label="Open menu"
                    >
                        <Menu className="h-5 w-5" />
                    </button>
                </Container>
            </div>

            <AnimatePresence>
                {open ? (
                    <motion.div
                        className="fixed inset-0 z-50 lg:hidden"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: reduce ? 0 : 0.2 }}
                    >
                        <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setOpen(false)} />
                        <motion.div
                            className="absolute right-0 top-0 flex h-full w-[82%] max-w-sm flex-col gap-2 border-l border-white/10 bg-[#0b1120] p-5 shadow-2xl"
                            initial={{ x: reduce ? 0 : '100%' }}
                            animate={{ x: 0 }}
                            exit={{ x: reduce ? 0 : '100%' }}
                            transition={{ type: 'tween', duration: reduce ? 0 : 0.28, ease: [0.22, 1, 0.36, 1] }}
                        >
                            <div className="flex items-center justify-between">
                                <BrandMark />
                                <button
                                    type="button"
                                    onClick={() => setOpen(false)}
                                    className="grid h-10 w-10 place-items-center rounded-lg border border-white/10 text-slate-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
                                    aria-label="Close menu"
                                >
                                    <X className="h-5 w-5" />
                                </button>
                            </div>
                            <nav className="mt-4 flex flex-col" aria-label="Mobile">
                                {navLinks.map((link) => (
                                    <a
                                        key={link.href}
                                        href={link.href}
                                        onClick={() => setOpen(false)}
                                        className="rounded-lg px-3 py-3 text-base font-medium text-slate-200 transition-colors hover:bg-white/5 hover:text-white"
                                    >
                                        {link.label}
                                    </a>
                                ))}
                            </nav>
                            <div className="mt-auto flex flex-col gap-3 border-t border-white/10 pt-5">
                                <LanguageSwitcher className="text-slate-200" />
                                <CTAButton variant="secondary" to="/login/choose" onClick={() => setOpen(false)}>
                                    {t('nav.login')}
                                </CTAButton>
                                <CTAButton to="/signup/super" onClick={() => setOpen(false)}>
                                    {t('nav.startFree')}
                                </CTAButton>
                            </div>
                        </motion.div>
                    </motion.div>
                ) : null}
            </AnimatePresence>
        </header>
    );
}
