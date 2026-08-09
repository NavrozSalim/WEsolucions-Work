import { useContext, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { AuthContext } from '../../context/AuthContext';
import LanguageSwitcher from '../../components/layout/LanguageSwitcher';
import { BrandMark } from '../../components/landing/Navbar';
import { Container, GridBackground } from '../../components/landing/primitives';
import { useI18n } from '../../context/I18nContext';
import { startSuperUserSignup, verifySuperUserOtp } from '../../services/authService';

const fieldClass =
    'block w-full rounded-xl border border-slate-500/40 bg-slate-800/65 px-3.5 py-2.5 text-sm text-white placeholder:text-slate-400 shadow-sm outline-none transition-colors focus:border-sky-400/60 focus:ring-2 focus:ring-sky-400/30';

const labelClass = 'mb-1.5 block text-sm font-medium text-slate-200';

function Field({ label, hint, id, ...props }) {
    return (
        <div>
            <label htmlFor={id} className={labelClass}>
                {label}
            </label>
            <input id={id} className={fieldClass} {...props} />
            {hint ? <p className="mt-1.5 text-xs text-slate-500">{hint}</p> : null}
        </div>
    );
}

export default function SuperUserSignup() {
    const { t } = useI18n();
    const navigate = useNavigate();
    const { setUserFromTokens } = useContext(AuthContext);

    // Pricing paused — all new Super Users get the default free seat pack.
    const seatLimit = 5;

    const [step, setStep] = useState('form');
    const [form, setForm] = useState({
        organization_name: '',
        first_name: '',
        last_name: '',
        email: '',
        password: '',
    });
    const [code, setCode] = useState('');
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);

    const onChange = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

    const sendCode = async (e) => {
        e?.preventDefault?.();
        setError('');
        setBusy(true);
        try {
            await startSuperUserSignup({
                ...form,
                seat_limit: seatLimit,
            });
            setStep('otp');
        } catch (err) {
            setError(err.response?.data?.detail || t('signup.sendError'));
        } finally {
            setBusy(false);
        }
    };

    const verify = async (e) => {
        e.preventDefault();
        setError('');
        setBusy(true);
        try {
            await verifySuperUserOtp(form.email.trim().toLowerCase(), code.trim());
            await setUserFromTokens();
            navigate('/team', { replace: true });
        } catch (err) {
            setError(err.response?.data?.detail || t('signup.verifyError'));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="spl-page min-h-screen">
            <div className="spl-aurora" aria-hidden />
            <GridBackground />

            <header className="relative z-20 border-b border-white/10 bg-[#060910]/70 backdrop-blur-xl">
                <Container className="flex h-16 items-center justify-between">
                    <BrandMark />
                    <LanguageSwitcher className="text-slate-200" />
                </Container>
            </header>

            <main className="relative z-10 mx-auto w-full max-w-md px-5 py-12 sm:py-16">
                <div className="rounded-2xl border border-white/10 bg-[#0b1120]/90 p-6 shadow-2xl backdrop-blur sm:p-8">
                    <h1 className="font-display text-2xl font-semibold tracking-tight text-white">
                        {step === 'otp' ? t('signup.verifyTitle') : t('signup.title')}
                    </h1>
                    <p className="mt-2 text-sm text-slate-400">
                        {t('signup.subtitle')}
                    </p>

                    {error ? (
                        <div
                            role="alert"
                            className="mt-4 rounded-xl border border-rose-400/30 bg-rose-400/10 px-3 py-2 text-sm text-rose-200"
                        >
                            {error}
                        </div>
                    ) : null}

                    {step === 'form' ? (
                        <form className="mt-6 space-y-4" onSubmit={sendCode}>
                            <Field
                                id="org"
                                label={t('signup.orgName')}
                                required
                                value={form.organization_name}
                                onChange={onChange('organization_name')}
                                autoComplete="organization"
                            />
                            <div className="grid grid-cols-2 gap-3">
                                <Field
                                    id="first"
                                    label={t('signup.firstName')}
                                    value={form.first_name}
                                    onChange={onChange('first_name')}
                                    autoComplete="given-name"
                                />
                                <Field
                                    id="last"
                                    label={t('signup.lastName')}
                                    value={form.last_name}
                                    onChange={onChange('last_name')}
                                    autoComplete="family-name"
                                />
                            </div>
                            <Field
                                id="email"
                                type="email"
                                label={t('signup.email')}
                                required
                                autoComplete="email"
                                value={form.email}
                                onChange={onChange('email')}
                                placeholder="you@company.com"
                                hint={t('signup.emailHint')}
                            />
                            <Field
                                id="password"
                                type="password"
                                label={t('signup.password')}
                                required
                                minLength={8}
                                value={form.password}
                                onChange={onChange('password')}
                                autoComplete="new-password"
                                hint={t('signup.passwordHint')}
                            />
                            <button
                                type="submit"
                                disabled={busy}
                                className="inline-flex w-full items-center justify-center rounded-xl bg-sky-500 px-4 py-3 text-sm font-semibold text-white shadow-[0_8px_30px_-8px_rgba(56,189,248,0.6)] transition-colors hover:bg-sky-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 disabled:opacity-60"
                            >
                                {busy ? t('signup.sending') : t('signup.sendCode')}
                            </button>
                            <p className="text-center text-sm">
                                <Link to="/login/super" className="font-medium text-sky-300 hover:text-sky-200">
                                    ← {t('login.signIn')}
                                </Link>
                            </p>
                        </form>
                    ) : (
                        <form className="mt-6 space-y-4" onSubmit={verify}>
                            <p className="text-sm text-slate-400">
                                {t('signup.verifyBody', { email: form.email })}
                            </p>
                            <Field
                                id="code"
                                label={t('signup.code')}
                                required
                                value={code}
                                onChange={(e) => setCode(e.target.value)}
                                placeholder="123456"
                                maxLength={6}
                                inputMode="numeric"
                                autoComplete="one-time-code"
                            />
                            <button
                                type="submit"
                                disabled={busy}
                                className="inline-flex w-full items-center justify-center rounded-xl bg-sky-500 px-4 py-3 text-sm font-semibold text-white shadow-[0_8px_30px_-8px_rgba(56,189,248,0.6)] transition-colors hover:bg-sky-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 disabled:opacity-60"
                            >
                                {busy ? t('signup.verifying') : t('signup.verify')}
                            </button>
                            <button
                                type="button"
                                className="w-full text-sm font-medium text-sky-300 hover:text-sky-200 disabled:opacity-60"
                                disabled={busy}
                                onClick={sendCode}
                            >
                                {t('signup.resend')}
                            </button>
                        </form>
                    )}
                </div>
            </main>
        </div>
    );
}
