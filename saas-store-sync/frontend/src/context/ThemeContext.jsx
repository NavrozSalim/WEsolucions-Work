import { createContext, useState, useEffect } from 'react';

export const ThemeContext = createContext(null);

const STORAGE_KEY = 'sellerpilothub-theme';
const LEGACY_STORAGE_KEY = 'wesolutions-theme';

function readStoredTheme() {
    const stored = localStorage.getItem(STORAGE_KEY) || localStorage.getItem(LEGACY_STORAGE_KEY);
    if (stored === 'light' || stored === 'dark') return stored === 'dark';
    return true; // default dark
}

export const ThemeProvider = ({ children }) => {
    const [dark, setDark] = useState(readStoredTheme);

    useEffect(() => {
        localStorage.setItem(STORAGE_KEY, dark ? 'dark' : 'light');
        document.documentElement.classList.toggle('dark', dark);
        document.documentElement.classList.toggle('light', !dark);
    }, [dark]);

    const toggleTheme = () => setDark((d) => !d);

    return (
        <ThemeContext.Provider value={{ dark, toggleTheme }}>
            {children}
        </ThemeContext.Provider>
    );
};
