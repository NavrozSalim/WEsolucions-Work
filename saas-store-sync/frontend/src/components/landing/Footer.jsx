import { Link } from 'react-router-dom';
import LanguageSwitcher from '../layout/LanguageSwitcher';
import { BrandMark } from './Navbar';
import { Container } from './primitives';
import { SALES_MAILTO } from './landingContent';
import { useI18n } from '../../context/I18nContext';

function FooterLink({ href, children }) {
    if (href.startsWith('#') || href.startsWith('mailto:')) {
        return (
            <a href={href} className="text-sm text-slate-400 transition-colors hover:text-white">
                {children}
            </a>
        );
    }
    return (
        <Link to={href} className="text-sm text-slate-400 transition-colors hover:text-white">
            {children}
        </Link>
    );
}

export default function Footer() {
    const { t } = useI18n();

    const columns = [
        {
            title: t('landing.footer.product'),
            links: [
                { label: t('nav.product'), href: '#product' },
                { label: t('nav.integrations'), href: '#integrations' },
                { label: t('nav.howItWorks'), href: '#how-it-works' },
            ],
        },
        {
            title: t('landing.footer.resources'),
            links: [
                { label: t('landing.footer.documentation'), href: '#' },
                { label: t('landing.footer.status'), href: '#' },
                { label: t('nav.faq'), href: '#faq' },
                { label: t('landing.footer.contact'), href: SALES_MAILTO },
            ],
        },
        {
            title: t('landing.footer.legal'),
            links: [
                { label: t('landing.footer.privacy'), href: '#' },
                { label: t('landing.footer.terms'), href: '#' },
            ],
        },
    ];

    return (
        <footer className="border-t border-white/10 bg-[#070b14]">
            <Container className="py-14">
                <div className="grid gap-10 lg:grid-cols-[1.4fr_repeat(3,1fr)]">
                    <div className="max-w-xs">
                        <BrandMark />
                        <p className="mt-4 text-sm leading-relaxed text-slate-400">{t('landing.footer.tagline')}</p>
                        <div className="mt-5">
                            <LanguageSwitcher className="text-slate-200" />
                        </div>
                    </div>

                    {columns.map((col) => (
                        <div key={col.title}>
                            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-500">{col.title}</h3>
                            <ul className="mt-4 space-y-3">
                                {col.links.map((link) => (
                                    <li key={link.label}>
                                        <FooterLink href={link.href}>{link.label}</FooterLink>
                                    </li>
                                ))}
                            </ul>
                        </div>
                    ))}
                </div>

                <div className="mt-12 flex flex-col items-center justify-between gap-4 border-t border-white/10 pt-6 sm:flex-row">
                    <p className="text-sm text-slate-500">{t('landing.footer.rights')}</p>
                    <div className="flex items-center gap-5">
                        <Link to="/login/choose" className="text-sm text-slate-400 transition-colors hover:text-white">
                            {t('nav.login')}
                        </Link>
                        <a href="#product" className="text-sm text-slate-400 transition-colors hover:text-white">
                            {t('landing.footer.backToTop')}
                        </a>
                    </div>
                </div>
            </Container>
        </footer>
    );
}
