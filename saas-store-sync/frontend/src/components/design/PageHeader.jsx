/**
 * Page section header — title + optional actions.
 */
export default function PageHeader({ title, description, actions }) {
    return (
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
                <h1 className="text-page-title font-semibold text-slate-900 dark:text-slate-100">
                    {title}
                </h1>
                {description && (
                    <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{description}</p>
                )}
            </div>
            {actions && <div className="flex flex-wrap items-center justify-end gap-2">{actions}</div>}
        </div>
    );
}
