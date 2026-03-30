import { useContext } from 'react';
import { ThemeContext } from '../../context/ThemeContext';

/**
 * Wesolutions logo mark — geometric, minimal.
 * Two connected blocks forming "W" abstraction.
 */
function LogoMark({ className = 'h-9 w-9', dark }) {
    const fill = dark ? '#94a3b8' : '#475569';
    return (
        <svg
            viewBox="0 0 24 24"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
            className={className}
            aria-hidden="true"
        >
            <path
                d="M4 18V6h2v5l2-3 2 3 2-3 2 3V6h2v12h-2v-5l-2 3-2-3-2 3v5H4z"
                fill={fill}
            />
        </svg>
    );
}

/**
 * Wesolutions — full logo (mark + wordmark).
 * Restrained, enterprise-ready.
 * iconOnly: mark only, for collapsed sidebar.
 * compact: mark + wordmark in one line.
 */
export function WesolutionsLogo({ compact = false, iconOnly = false }) {
    const theme = useContext(ThemeContext);
    const dark = theme?.dark ?? true;

    if (iconOnly) {
        return (
            <div
                className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-md ${
                    dark ? 'bg-slate-800' : 'bg-slate-100'
                }`}
                aria-hidden="true"
            >
                <LogoMark className="h-5 w-5" dark={dark} />
            </div>
        );
    }

    if (compact) {
        return (
            <div className="flex items-center gap-2">
                <div
                    className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md ${
                        dark ? 'bg-slate-800' : 'bg-slate-100'
                    }`}
                >
                    <LogoMark className="h-5 w-5" dark={dark} />
                </div>
                <span
                    className={`text-[15px] font-semibold tracking-tight ${
                        dark ? 'text-slate-100' : 'text-slate-900'
                    }`}
                >
                    Wesolutions
                </span>
            </div>
        );
    }

    return (
        <div className="flex items-center gap-3">
            <div
                className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-md ${
                    dark ? 'bg-slate-800' : 'bg-slate-100'
                }`}
            >
                <LogoMark className="h-6 w-6" dark={dark} />
            </div>
            <div className="leading-tight">
                <p className={`text-[17px] font-semibold tracking-tight ${dark ? 'text-slate-100' : 'text-slate-900'}`}>
                    Wesolutions
                </p>
                <p className={`text-[11px] font-medium uppercase tracking-widest ${dark ? 'text-slate-500' : 'text-slate-400'}`}>
                    Store Sync
                </p>
            </div>
        </div>
    );
}
