import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { ArrowRight, Shield, UserRound } from 'lucide-react';
import LanguageSwitcher from '../../components/layout/LanguageSwitcher';
import { BrandMark } from '../../components/landing/Navbar';
import { Container, GridBackground } from '../../components/landing/primitives';
import { useI18n } from '../../context/I18nContext';

export default function LoginChooser() {
    const { t } = useI18n();

    const options = [
        {
            to: '/login/user',
            icon: UserRound,
            title: t('loginChooser.userTitle'),
            body: t('loginChooser.userBody'),
        },
        {
            to: '/login/super',
            icon: Shield,
            title: t('loginChooser.superTitle'),
            body: t('loginChooser.superBody'),
        },
    ];

    return (
        <div className="spl-page min-h-screen">
            <div className="spl-aurora" aria-hidden />
            <GridBackground />

            <header className="relative z-20 border-b border-white/10 bg-[#060910]/70 backdrop-blur-xl">
                <Container className="flex h-16 items-center justify-between">
                    <BrandMark />
                    <LanguageSwitcher className="text-slate-200" />
                </Container>
            </header>

            <main className="relative z-10">
                <Container className="max-w-3xl py-14 sm:py-16">
                    <motion.h1
                        className="font-display text-3xl font-semibold tracking-tight text-white sm:text-4xl"
                        initial={{ opacity: 0, y: 12 }}
                        animate={{ opacity: 1, y: 0 }}
                    >
                        {t('loginChooser.title')}
                    </motion.h1>

                    <div className="mt-8 grid gap-4 sm:grid-cols-2">
                        {options.map((opt, i) => {
                            const Icon = opt.icon;
                            return (
                                <motion.div
                                    key={opt.to}
                                    initial={{ opacity: 0, y: 14 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    transition={{ delay: i * 0.08 }}
                                >
                                    <Link
                                        to={opt.to}
                                        className="group flex h-full flex-col rounded-2xl border border-white/10 bg-white/[0.02] p-6 transition-colors hover:border-sky-400/40 hover:bg-sky-400/[0.04] focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
                                    >
                                        <span className="grid h-11 w-11 place-items-center rounded-xl border border-white/10 bg-sky-400/10 text-sky-300">
                                            <Icon className="h-5 w-5" />
                                        </span>
                                        <h2 className="mt-5 font-display text-lg font-semibold text-white">
                                            {opt.title}
                                        </h2>
                                        <p className="mt-2 flex-1 text-sm leading-relaxed text-slate-400">{opt.body}</p>
                                        <span className="mt-5 inline-flex items-center gap-1.5 text-sm font-semibold text-sky-300 group-hover:text-sky-200">
                                            {t('login.signIn')} <ArrowRight className="h-4 w-4" />
                                        </span>
                                    </Link>
                                </motion.div>
                            );
                        })}
                    </div>

                    <p className="mt-8">
                        <Link to="/" className="text-sm font-medium text-slate-400 transition-colors hover:text-white">
                            ← {t('loginChooser.back')}
                        </Link>
                    </p>
                </Container>
            </main>
        </div>
    );
}
