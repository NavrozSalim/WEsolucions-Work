/**
 * Toggle switch — clean, accessible, no external deps.
 */
export default function Toggle({ checked, onChange, disabled = false }) {
    return (
        <button
            type="button"
            role="switch"
            aria-checked={checked}
            disabled={disabled}
            onClick={() => !disabled && onChange?.(!checked)}
            className={[
                'relative inline-flex h-[22px] w-[42px] shrink-0 items-center rounded-full transition-colors duration-200',
                checked
                    ? 'bg-accent-600 dark:bg-accent-500'
                    : 'bg-slate-200 dark:bg-slate-700',
                disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer',
            ].join(' ')}
        >
            <span
                aria-hidden="true"
                className={[
                    'pointer-events-none inline-block h-[18px] w-[18px] rounded-full bg-white shadow-sm ring-0 transition-transform duration-200',
                    checked ? 'translate-x-[22px]' : 'translate-x-[2px]',
                ].join(' ')}
            />
        </button>
    );
}
