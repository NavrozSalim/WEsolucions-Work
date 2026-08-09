import { useI18n } from '../../context/I18nContext';

export default function LanguageSwitcher({ className = '' }) {
    const { locale, setLocale, locales, t } = useI18n();

    return (
        <label className={`inline-flex items-center gap-2 text-sm ${className}`}>
            <span className="sr-only">{t('nav.language')}</span>
            <select
                value={locale}
                onChange={(e) => setLocale(e.target.value)}
                className="rounded-lg border border-white/15 bg-white/5 px-2.5 py-1.5 text-sm text-slate-100 outline-none backdrop-blur transition-colors hover:border-white/25 focus:ring-2 focus:ring-sky-400/60"
                aria-label={t('nav.language')}
            >
                {locales.map((loc) => (
                    <option key={loc.code} value={loc.code} className="bg-[#0b1120] text-slate-100">
                        {loc.label}
                    </option>
                ))}
            </select>
        </label>
    );
}
