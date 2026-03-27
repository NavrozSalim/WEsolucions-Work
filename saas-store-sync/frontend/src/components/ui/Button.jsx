/**
 * Button component — B2B SaaS style with strong dark mode contrast.
 *
 * Best practices applied:
 * - Explicit dark: variants for every state (bg, text, border, hover, focus)
 * - Primary: saturated accent with white text in both themes
 * - Secondary: elevated surface with clear border and dark/light text
 * - Ghost: subtle hover, readable text on any background
 * - Danger: high-contrast destructive action
 * - focus:ring-offset matches background so ring is visible
 */
const variants = {
    primary: [
        'bg-accent-600 text-white',
        'hover:bg-accent-700',
        'focus:ring-2 focus:ring-accent-500 focus:ring-offset-2 focus:ring-offset-white dark:focus:ring-offset-slate-900',
        'dark:bg-accent-500 dark:text-white dark:hover:bg-accent-400',
        'border border-transparent',
    ].join(' '),
    secondary: [
        'bg-white text-slate-900',
        'hover:bg-slate-50',
        'border border-slate-200',
        'focus:ring-2 focus:ring-slate-400 focus:ring-offset-2 focus:ring-offset-white dark:focus:ring-offset-slate-900',
        'dark:bg-slate-800 dark:text-slate-100 dark:border-slate-600 dark:hover:bg-slate-700',
    ].join(' '),
    ghost: [
        'text-slate-700 bg-transparent',
        'hover:bg-slate-100',
        'border border-transparent',
        'focus:ring-2 focus:ring-slate-400 focus:ring-offset-2 focus:ring-offset-white dark:focus:ring-offset-slate-900',
        'dark:text-slate-200 dark:hover:bg-slate-800',
    ].join(' '),
    danger: [
        'bg-rose-600 text-white',
        'hover:bg-rose-700',
        'focus:ring-2 focus:ring-rose-500 focus:ring-offset-2 focus:ring-offset-white dark:focus:ring-offset-slate-900',
        'dark:bg-rose-600 dark:text-white dark:hover:bg-rose-500',
        'border border-transparent',
    ].join(' '),
};

const sizes = {
    sm: 'px-3 py-1.5 text-sm',
    md: 'px-4 py-2 text-sm',
    lg: 'px-6 py-3 text-base',
};

const baseStyles =
    'inline-flex items-center justify-center font-medium rounded-md focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed transition-colors';

export default function Button({
    children,
    variant = 'primary',
    size = 'md',
    type = 'button',
    disabled = false,
    className = '',
    ...props
}) {
    return (
        <button
            type={type}
            disabled={disabled}
            className={`${baseStyles} ${variants[variant]} ${sizes[size]} ${className}`.trim()}
            {...props}
        >
            {children}
        </button>
    );
}
