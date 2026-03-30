export default function Card({ title, children, className = '' }) {
    return (
        <div className={`rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-sm ${className}`}>
            {title && (
                <div className="px-6 py-4 border-b border-gray-100 dark:border-gray-700">
                    <h3 className="text-sm font-medium text-gray-500 dark:text-gray-400">{title}</h3>
                </div>
            )}
            <div className={title ? 'p-6' : 'p-6'}>{children}</div>
        </div>
    );
}
