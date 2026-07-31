import { useContext } from 'react';
import { ThemeContext } from '../../context/ThemeContext';

/**
 * SellerPilot Hub mark — sync arcs + centered hub (store sync / pilot).
 */
function LogoMark({ className = 'h-9 w-9', dark }) {
    const stroke = dark ? '#e2e8f0' : '#0f172a';
    const accent = dark ? '#38bdf8' : '#0369a1';
    return (
        <svg
            viewBox="0 0 32 32"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
            className={className}
            aria-hidden="true"
        >
            <rect x="2" y="2" width="28" height="28" rx="8" fill={dark ? '#0f172a' : '#f1f5f9'} />
            <path
                d="M10 16c0-3.3 2.7-6 6-6 1.8 0 3.4.8 4.5 2"
                stroke={accent}
                strokeWidth="2"
                strokeLinecap="round"
            />
            <path
                d="M22 16c0 3.3-2.7 6-6 6-1.8 0-3.4-.8-4.5-2"
                stroke={stroke}
                strokeWidth="2"
                strokeLinecap="round"
                opacity="0.85"
            />
            <circle cx="16" cy="16" r="2.4" fill={accent} />
            <path d="M20.5 9.5l2.2-.3-.3 2.2" stroke={accent} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M11.5 22.5l-2.2.3.3-2.2" stroke={stroke} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" opacity="0.85" />
        </svg>
    );
}

/**
 * SellerPilot Hub — mark + wordmark.
 * iconOnly: mark only (collapsed sidebar)
 * compact: single-line mark + name
 */
export function SellerPilotHubLogo({ compact = false, iconOnly = false }) {
    const theme = useContext(ThemeContext);
    const dark = theme?.dark ?? true;

    if (iconOnly) {
        return (
            <div className="flex h-9 w-9 shrink-0 items-center justify-center" aria-hidden="true">
                <LogoMark className="h-9 w-9" dark={dark} />
            </div>
        );
    }

    if (compact) {
        return (
            <div className="flex items-center gap-2.5 min-w-0">
                <LogoMark className="h-8 w-8 shrink-0" dark={dark} />
                <div className="min-w-0 leading-tight">
                    <p
                        className={`truncate text-[15px] font-semibold tracking-tight ${
                            dark ? 'text-slate-100' : 'text-slate-900'
                        }`}
                    >
                        SellerPilot Hub
                    </p>
                </div>
            </div>
        );
    }

    return (
        <div className="flex items-center gap-3">
            <LogoMark className="h-11 w-11 shrink-0" dark={dark} />
            <div className="leading-tight">
                <p className={`text-[18px] font-semibold tracking-tight ${dark ? 'text-slate-100' : 'text-slate-900'}`}>
                    SellerPilot Hub
                </p>
                <p
                    className={`text-[11px] font-medium uppercase tracking-[0.14em] ${
                        dark ? 'text-slate-500' : 'text-slate-400'
                    }`}
                >
                    Store Sync
                </p>
            </div>
        </div>
    );
}

/** @deprecated Use SellerPilotHubLogo */
export const WesolutionsLogo = SellerPilotHubLogo;
