/**
 * Empty state — when no data to display.
 */
export default function EmptyState({ icon: Icon, title, description, action }) {
    return (
        <div className="flex flex-col items-center justify-center py-16 px-4 text-center">
            {Icon && (
                <div className="rounded-full bg-slate-100 dark:bg-slate-800 p-4 mb-4">
                    <Icon className="h-8 w-8 text-slate-400 dark:text-slate-500" />
                </div>
            )}
            <h3 className="text-base font-medium text-slate-900 dark:text-slate-100">{title}</h3>
            {description && (
                <p className="mt-1 text-sm text-slate-500 dark:text-slate-400 max-w-sm">
                    {description}
                </p>
            )}
            {action && <div className="mt-4">{action}</div>}
        </div>
    );
}
