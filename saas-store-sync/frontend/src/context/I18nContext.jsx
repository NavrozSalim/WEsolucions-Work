import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { SUPPORTED_LOCALES, interpolate, translations } from '../i18n/translations';

const STORAGE_KEY = 'sellerpilothub-locale';

export const I18nContext = createContext(null);

export function I18nProvider({ children }) {
    const [locale, setLocaleState] = useState(() => {
        const saved = localStorage.getItem(STORAGE_KEY);
        if (saved && translations[saved]) return saved;
        return 'en';
    });

    useEffect(() => {
        localStorage.setItem(STORAGE_KEY, locale);
        document.documentElement.lang = locale;
    }, [locale]);

    const setLocale = useCallback((code) => {
        if (translations[code]) setLocaleState(code);
    }, []);

    const t = useCallback(
        (path, vars) => {
            const parts = path.split('.');
            let node = translations[locale] || translations.en;
            for (const part of parts) {
                node = node?.[part];
            }
            if (typeof node !== 'string') {
                let fallback = translations.en;
                for (const part of parts) fallback = fallback?.[part];
                node = typeof fallback === 'string' ? fallback : path;
            }
            return vars ? interpolate(node, vars) : node;
        },
        [locale],
    );

    const value = useMemo(
        () => ({ locale, setLocale, t, locales: SUPPORTED_LOCALES }),
        [locale, setLocale, t],
    );

    return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
    const ctx = useContext(I18nContext);
    if (!ctx) throw new Error('useI18n must be used within I18nProvider');
    return ctx;
}
