import { SellerPilotHubLogo } from './SellerPilotHubLogo';

/** Full-screen brand loading state. */
export function SellerPilotHubLoading() {
    return (
        <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-slate-50 dark:bg-slate-950">
            <div className="animate-pulse">
                <SellerPilotHubLogo />
            </div>
            <p className="text-sm text-slate-500 dark:text-slate-400">Loading…</p>
        </div>
    );
}

/** @deprecated Use SellerPilotHubLoading */
export const WesolutionsLoading = SellerPilotHubLoading;
