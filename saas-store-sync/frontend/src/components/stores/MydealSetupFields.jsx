import { UploadCloud } from 'lucide-react';
import Button from '../ui/Button';
import Select from '../ui/Select';
import MydealConnectionFields from './MydealConnectionFields';

/**
 * MyDeal setup: connection method + template upload or API credentials.
 * storeId: set when editing an existing store; omit during create until store is saved.
 */
export default function MydealSetupFields({
    setupMethod,
    onSetupMethodChange,
    storeId,
    onOpenUpload,
    form,
    setForm,
    mode = 'edit',
    /** Managed (full_store) MyDeal requires API — hide upload option. */
    forceApi = false,
}) {
    const effectiveMethod = forceApi ? 'api' : ((setupMethod || 'upload') === 'api' ? 'api' : 'upload');
    const isUpload = effectiveMethod === 'upload';
    const isApi = effectiveMethod === 'api';

    return (
        <div className="space-y-3">
            {!forceApi && (
                <Select
                    label="MyDeal connection method"
                    value={setupMethod || 'upload'}
                    onChange={(e) => onSetupMethodChange(e.target.value)}
                    options={[
                        { value: 'upload', label: 'Upload Option (Price & Inventory templates)' },
                        { value: 'api', label: 'API connection (Managed store / WMP Universal API)' },
                    ]}
                    required
                />
            )}
            {forceApi && (
                <p className="text-sm font-medium text-slate-700 dark:text-slate-300">
                    MyDeal managed store uses the WMP Universal API
                </p>
            )}
            {isApi && form && setForm && (
                <MydealConnectionFields form={form} setForm={setForm} mode={mode} />
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
                Use <strong>Store name</strong> for your MyDeal store (e.g. TFS, P&amp;P). Credentials are encrypted at rest.
                MyDeal is also known as Woolworths Marketplace (WMP).
            </p>
        </div>
    );
}
