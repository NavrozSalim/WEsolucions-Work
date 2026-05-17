import { UploadCloud } from 'lucide-react';
import Button from '../ui/Button';
import Select from '../ui/Select';

/**
 * Kogan-style Mydeal setup: connection method + template upload when "upload" is selected.
 * storeId: set when editing an existing store; omit during create until store is saved.
 */
export default function MydealSetupFields({
    setupMethod,
    onSetupMethodChange,
    storeId,
    onOpenUpload,
}) {
    const isUpload = (setupMethod || 'upload') === 'upload';

    return (
        <div className="space-y-3">
            <Select
                label="Mydeal connection method"
                value={setupMethod || 'upload'}
                onChange={(e) => onSetupMethodChange(e.target.value)}
                options={[
                    { value: 'upload', label: 'Upload Option (Price & Inventory templates)' },
                    { value: 'api', label: 'API connection (coming soon)' },
                ]}
                required
            />
            {setupMethod === 'api' && (
                <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 px-4 py-3 text-sm text-amber-800 dark:text-amber-300">
                    API integration is not active yet. Use Upload Option and upload your Mydeal Price and Inventory CSV templates.
                </div>
            )}
            {isUpload && (
                <div className="space-y-2">
                    <Button
                        type="button"
                        variant="secondary"
                        className="w-full sm:w-auto"
                        onClick={onOpenUpload}
                        disabled={!storeId}
                    >
                        <UploadCloud className="h-4 w-4 mr-1.5 inline" aria-hidden />
                        Upload / replace templates
                    </Button>
                    {!storeId ? (
                        <p className="text-xs text-slate-500 dark:text-slate-400">
                            Enter your store name (e.g. TFS or P&amp;P), finish setup, then use this button — or upload opens automatically after you create the store.
                        </p>
                    ) : (
                        <p className="text-xs text-slate-500 dark:text-slate-400">
                            Upload or replace Price and Inventory CSVs anytime. Each file overwrites that template for this store. Re-scrape after SKU changes, then download templates.
                        </p>
                    )}
                </div>
            )}
            <p className="text-xs text-slate-500 dark:text-slate-400">
                Use <strong>Store name</strong> for your Mydeal store (e.g. TFS, P&amp;P). Credentials are encrypted at rest.
            </p>
        </div>
    );
}
