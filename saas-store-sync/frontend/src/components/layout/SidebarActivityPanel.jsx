import { useContext, useMemo } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Loader2 } from 'lucide-react';
import { ThemeContext } from '../../context/ThemeContext';

export default function SidebarActivityPanel({ activities, desktopCollapsed }) {
    const theme = useContext(ThemeContext);
    const dark = theme?.dark ?? true;

    const list = useMemo(
        () => Object.values(activities).sort((a, b) => a.id.localeCompare(b.id)),
        [activities],
    );

    const hasAny = list.length > 0;

    if (desktopCollapsed) {
        return (
            <div className="flex min-h-0 flex-1 flex-col items-center justify-center px-1 py-2" aria-live="polite">
                <AnimatePresence mode="popLayout">
                    {hasAny && (
                        <motion.div
                            key="collapsed-activity"
                            initial={{ opacity: 0, scale: 0.9 }}
                            animate={{ opacity: 1, scale: 1 }}
                            exit={{ opacity: 0, scale: 0.9 }}
                            transition={{ duration: 0.15 }}
                            title={list.map((a) => a.title).join(' · ')}
                        >
                            <Loader2 className="h-5 w-5 shrink-0 animate-spin text-accent-500" aria-hidden />
                            <span className="sr-only">Background activity: {list.map((a) => a.title).join(', ')}</span>
                        </motion.div>
                    )}
                </AnimatePresence>
            </div>
        );
    }

    return (
        <div
            className="flex min-h-0 flex-1 flex-col justify-end px-3 pb-2 pt-2"
            aria-live="polite"
            aria-label="Background activity"
        >
            <AnimatePresence mode="popLayout">
                {list.map((a) => (
                    <motion.div
                        key={a.id}
                        layout
                        initial={{ opacity: 0, y: 8 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -6 }}
                        transition={{ duration: 0.18 }}
                        className={`mb-2 rounded-lg border px-3 py-2.5 text-left shadow-sm last:mb-0 ${
                            dark
                                ? 'border-slate-700/80 bg-slate-900/90 text-slate-200'
                                : 'border-slate-200 bg-white text-slate-800'
                        }`}
                    >
                        <div className="flex items-start gap-2">
                            <Loader2
                                className={`mt-0.5 h-4 w-4 shrink-0 animate-spin ${
                                    dark ? 'text-accent-400' : 'text-accent-600'
                                }`}
                                aria-hidden
                            />
                            <div className="min-w-0 flex-1">
                                <p className="text-xs font-semibold leading-tight">{a.title}</p>
                                {a.description ? (
                                    <p
                                        className={`mt-1 text-[11px] leading-snug ${
                                            dark ? 'text-slate-400' : 'text-slate-600'
                                        }`}
                                    >
                                        {a.description}
                                    </p>
                                ) : null}
                                {a.progress != null && a.progress > 0 ? (
                                    <div
                                        className={`mt-2 h-1 w-full overflow-hidden rounded-full ${
                                            dark ? 'bg-slate-800' : 'bg-slate-200'
                                        }`}
                                    >
                                        <div
                                            className={`h-full rounded-full transition-all duration-300 ${
                                                dark ? 'bg-accent-500' : 'bg-accent-600'
                                            }`}
                                            style={{ width: `${Math.min(100, a.progress)}%` }}
                                        />
                                    </div>
                                ) : null}
                                {a.progress != null && a.progress > 0 ? (
                                    <p
                                        className={`mt-1 text-[10px] tabular-nums ${
                                            dark ? 'text-slate-500' : 'text-slate-500'
                                        }`}
                                    >
                                        {Math.round(a.progress)}%
                                    </p>
                                ) : null}
                            </div>
                        </div>
                    </motion.div>
                ))}
            </AnimatePresence>
        </div>
    );
}
