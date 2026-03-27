export function Table({ children, className = '' }) {
    return (
        <div className={`overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700 ${className}`}>
            <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">{children}</table>
        </div>
    );
}

export function TableHead({ children }) {
    return (
        <thead className="bg-gray-50 dark:bg-gray-800">
            <tr>{children}</tr>
        </thead>
    );
}

export function TableBody({ children }) {
    return <tbody className="divide-y divide-gray-200 dark:divide-gray-700 bg-white dark:bg-gray-800">{children}</tbody>;
}

export function Th({ children, className = '' }) {
    return (
        <th scope="col" className={`px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider ${className}`}>
            {children}
        </th>
    );
}

export function Td({ children, className = '' }) {
    return (
        <td className={`px-6 py-4 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100 ${className}`}>
            {children}
        </td>
    );
}

export function TableEmpty({ colSpan, message = 'No data' }) {
    return (
        <tr>
            <td colSpan={colSpan} className="px-6 py-12 text-center text-sm text-gray-500 dark:text-gray-400">
                {message}
            </td>
        </tr>
    );
}
