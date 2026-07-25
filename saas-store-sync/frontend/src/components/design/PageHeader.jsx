/**
 * Page section header — title + optional actions.
 */
export default function PageHeader({ title, description, actions }) {
    return (
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
            <div className="min-w-0">
                <h1 className="text-xl font-semibold tracking-tight text-slate-900 dark:text-slate-100 sm:text-page-title">
                    {title}
                </h1>
                {description && (
                    <p className="mt-1 text-sm text-slate-500 dark:text-slate-400 line-clamp-2 sm:line-clamp-none">
                        {description}
                    </p>
                )}
            </div>
            {actions && (
                <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto sm:justify-end">
                    {actions}
                </div>
            )}
        </div>
    );
}
