import { Link } from 'react-router-dom';
import { motion, useReducedMotion } from 'framer-motion';

export function Container({ className = '', children }) {
    return <div className={`mx-auto w-full max-w-6xl px-5 sm:px-6 lg:px-8 ${className}`}>{children}</div>;
}

// Section wrapper with an id anchor and consistent vertical rhythm.
export function Section({ id, className = '', children }) {
    return (
        <section id={id} className={`relative scroll-mt-24 py-20 sm:py-24 ${className}`}>
            {children}
        </section>
    );
}

export function Eyebrow({ children, className = '' }) {
    return (
        <span
            className={`inline-flex items-center gap-2 rounded-full border border-sky-400/25 bg-sky-400/10 px-3 py-1 text-xs font-medium uppercase tracking-wider text-sky-300 ${className}`}
        >
            {children}
        </span>
    );
}

export function SectionHeading({ eyebrow, title, subtitle, align = 'center' }) {
    const alignment = align === 'center' ? 'text-center mx-auto' : 'text-left';
    return (
        <div className={`max-w-2xl ${alignment}`}>
            {eyebrow ? <Eyebrow>{eyebrow}</Eyebrow> : null}
            <h2 className="mt-4 font-display text-3xl font-semibold tracking-tight text-white sm:text-4xl">
                {title}
            </h2>
            {subtitle ? <p className="mt-4 text-base leading-relaxed text-slate-400">{subtitle}</p> : null}
        </div>
    );
}

// Reveal-on-scroll wrapper. Falls back to static when reduced motion is requested.
export function Reveal({ children, delay = 0, y = 16, className = '', as = 'div' }) {
    const reduce = useReducedMotion();
    const MotionTag = motion[as] || motion.div;
    if (reduce) {
        const Tag = as;
        return <Tag className={className}>{children}</Tag>;
    }
    return (
        <MotionTag
            className={className}
            initial={{ opacity: 0, y }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: '-80px' }}
            transition={{ duration: 0.5, delay, ease: [0.22, 1, 0.36, 1] }}
        >
            {children}
        </MotionTag>
    );
}

const CTA_VARIANTS = {
    primary:
        'bg-sky-500 text-white shadow-[0_8px_30px_-8px_rgba(56,189,248,0.6)] hover:bg-sky-400',
    secondary:
        'border border-white/15 bg-white/5 text-slate-100 hover:border-white/25 hover:bg-white/10',
    ghost: 'text-slate-300 hover:text-white',
};

const CTA_BASE =
    'inline-flex items-center justify-center gap-2 rounded-xl px-5 py-3 text-sm font-semibold transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 focus-visible:ring-offset-2 focus-visible:ring-offset-[#060910] disabled:opacity-60';

// Polymorphic CTA: internal route (to), anchor (href), or button (onClick).
export function CTAButton({ variant = 'primary', to, href, onClick, className = '', children, ...rest }) {
    const classes = `${CTA_BASE} ${CTA_VARIANTS[variant] || CTA_VARIANTS.primary} ${className}`;
    if (to) {
        return (
            <Link to={to} className={classes} {...rest}>
                {children}
            </Link>
        );
    }
    if (href) {
        return (
            <a href={href} className={classes} {...rest}>
                {children}
            </a>
        );
    }
    return (
        <button type="button" onClick={onClick} className={classes} {...rest}>
            {children}
        </button>
    );
}

// Faint dotted/grid backdrop used behind hero and section bands.
export function GridBackground({ className = '' }) {
    return (
        <div aria-hidden className={`pointer-events-none absolute inset-0 spl-grid spl-grid-mask ${className}`} />
    );
}
