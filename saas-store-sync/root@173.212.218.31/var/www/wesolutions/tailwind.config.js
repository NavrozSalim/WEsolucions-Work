/** @type {import('tailwindcss').Config} */
export default {
    content: [
        "./index.html",
        "./src/**/*.{js,ts,jsx,tsx}",
    ],
    safelist: ['bg-green-500', 'bg-red-500', 'bg-slate-500'],
    darkMode: 'class',
    theme: {
        extend: {
            colors: {
                // Single accent: blue, restrained
                accent: {
                    50: '#eff6ff',
                    100: '#dbeafe',
                    200: '#bfdbfe',
                    300: '#93c5fd',
                    400: '#60a5fa',
                    500: '#3b82f6',
                    600: '#2563eb',
                    700: '#1d4ed8',
                    800: '#1e40af',
                    900: '#1e3a8a',
                },
                // Surface tokens for consistent cards
                surface: {
                    light: '#ffffff',
                    dark: '#0f172a',
                },
            },
            fontFamily: {
                sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'sans-serif'],
            },
            fontSize: {
                'display': ['2rem', { lineHeight: '1.2' }],
                'page-title': ['1.5rem', { lineHeight: '1.3' }],
                'section': ['1rem', { lineHeight: '1.5' }],
            },
            borderRadius: {
                'sm': '6px',
                'md': '8px',
                'lg': '12px',
                'xl': '16px',
            },
            boxShadow: {
                'card': '0 1px 3px 0 rgb(0 0 0 / 0.05), 0 1px 2px -1px rgb(0 0 0 / 0.05)',
                'card-dark': '0 1px 3px 0 rgb(0 0 0 / 0.2)',
                'dropdown': '0 4px 6px -1px rgb(0 0 0 / 0.08), 0 2px 4px -2px rgb(0 0 0 / 0.04)',
                'modal': '0 10px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.05)',
            },
            spacing: {
                '18': '4.5rem',
                'sidebar': '240px',
            },
        },
    },
    plugins: [],
};
