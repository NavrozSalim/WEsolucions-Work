import { useEffect, useState, useRef } from 'react';
import { motion } from 'framer-motion';
import { Check, X, Info } from 'lucide-react';

export default function Toast({ open, message, variant = 'info', duration = 4000, onClose }) {
    const [progress, setProgress] = useState(100);
    const timerRef = useRef(null);
    const v = variant === 'failed' ? 'error' : variant;

    useEffect(() => {
        if (!open || !onClose) return;
        setProgress(100);
        const start = Date.now();
        timerRef.current = setInterval(() => {
            const elapsed = Date.now() - start;
            const remaining = Math.max(0, 100 - (elapsed / duration) * 100);
            setProgress(remaining);
            if (remaining <= 0) {
                if (timerRef.current) clearInterval(timerRef.current);
                onClose();
            }
        }, 16);
        return () => {
            if (timerRef.current) clearInterval(timerRef.current);
        };
    }, [open, duration, onClose]);

    if (!open) return null;

    return (
        <motion.div
            initial={{ opacity: 0, x: 80 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 80 }}
            transition={{ type: 'spring', damping: 28, stiffness: 350 }}
            className="fixed top-4 right-4 z-[100] w-full max-w-md"
        >
            <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 shadow-lg overflow-hidden">
                <div className="flex items-start gap-3 p-4">
                    <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-white ${
                        v === 'success' ? 'bg-green-500' : v === 'error' ? 'bg-red-500' : 'bg-slate-500'
                    }`}>
                        {v === 'success' && <Check className="h-5 w-5 stroke-[2.5]" />}
                        {v === 'error' && <X className="h-5 w-5 stroke-[2.5]" />}
                        {v !== 'success' && v !== 'error' && <Info className="h-5 w-5 stroke-[2.5]" />}
                    </div>
                    <p className="flex-1 text-sm font-medium text-slate-900 dark:text-slate-100 pt-0.5 break-words">
                        {message}
                    </p>
                    <button
                        type="button"
                        onClick={onClose}
                        className="shrink-0 rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-300 transition"
                        aria-label="Close"
                    >
                        <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
                    </button>
                </div>
                <div className="h-1 bg-slate-100 dark:bg-slate-800 overflow-hidden">
                    <div
                        className={`h-full transition-[width] duration-75 ${
                            v === 'success' ? 'bg-green-500' : v === 'error' ? 'bg-red-500' : 'bg-slate-500'
                        }`}
                        style={{ width: `${progress}%` }}
                    />
                </div>
            </div>
        </motion.div>
    );
}
