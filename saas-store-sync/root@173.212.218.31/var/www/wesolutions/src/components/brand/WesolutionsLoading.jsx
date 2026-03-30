import { useContext } from 'react';
import { motion } from 'framer-motion';
import { WesolutionsLogo } from './WesolutionsLogo';
import { ThemeContext } from '../../context/ThemeContext';

/**
 * Professional loading screen — calm, restrained.
 * No flashy animations or fake progress.
 */
export function WesolutionsLoading() {
    const theme = useContext(ThemeContext);
    const dark = theme?.dark ?? true;

    return (
        <div
            className={`flex min-h-screen items-center justify-center ${
                dark ? 'bg-slate-950' : 'bg-slate-50'
            }`}
            role="status"
            aria-live="polite"
            aria-label="Loading"
        >
            <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ duration: 0.2 }}
                className="flex flex-col items-center gap-8"
            >
                <WesolutionsLogo />
                <div className="flex items-center gap-2">
                    <div
                        className={`h-1.5 w-1.5 rounded-full ${
                            dark ? 'bg-slate-500' : 'bg-slate-400'
                        }`}
                        style={{ animation: 'pulse 1.5s ease-in-out infinite' }}
                    />
                    <div
                        className={`h-1.5 w-1.5 rounded-full ${
                            dark ? 'bg-slate-500' : 'bg-slate-400'
                        }`}
                        style={{ animation: 'pulse 1.5s ease-in-out 0.2s infinite' }}
                    />
                    <div
                        className={`h-1.5 w-1.5 rounded-full ${
                            dark ? 'bg-slate-500' : 'bg-slate-400'
                        }`}
                        style={{ animation: 'pulse 1.5s ease-in-out 0.4s infinite' }}
                    />
                </div>
                <p className={`text-sm ${dark ? 'text-slate-500' : 'text-slate-400'}`}>
                    Loading…
                </p>
            </motion.div>
        </div>
    );
}
