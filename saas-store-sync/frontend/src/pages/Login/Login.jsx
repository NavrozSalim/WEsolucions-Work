import { useState, useContext, useEffect } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { AuthContext } from '../../context/AuthContext';
import { ThemeContext } from '../../context/ThemeContext';
import { WesolutionsLogo } from '../../components/brand';
import Input from '../../components/ui/Input';
import Button from '../../components/ui/Button';
import { Sun, Moon } from 'lucide-react';

const ERROR_MESSAGES = {
    invalid_state: 'Google sign-in session expired. Please try again.',
    no_code: 'Google did not return an authorization code. Please try again.',
    oauth_not_configured: 'Google sign-in is not configured.',
    no_email: 'Could not get your email from Google.',
};

const Login = () => {
    const { dark, toggleTheme } = useContext(ThemeContext);
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState('');
    const [searchParams] = useSearchParams();
    const { login } = useContext(AuthContext);
    const navigate = useNavigate();

    useEffect(() => {
        const errorParam = searchParams.get('error');
        if (errorParam) {
            setError(ERROR_MESSAGES[errorParam] || `Sign-in error: ${errorParam}`);
        }
    }, [searchParams]);

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');
        try {
            await login(email, password);
            navigate('/');
        } catch (err) {
            if (!err.response) {
                setError('Cannot reach the server. Is the backend running at ' + (import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1') + '?');
            } else {
                setError(err.response?.data?.detail || 'Invalid credentials');
            }
        }
    };

    return (
        <div className="min-h-screen flex items-center justify-center py-12 px-4 sm:px-6 lg:px-8 bg-slate-50 dark:bg-slate-950">
            <button
                type="button"
                onClick={toggleTheme}
                className="fixed top-4 right-4 z-10 p-2 rounded-md hover:bg-slate-200 dark:hover:bg-slate-800 text-slate-600 dark:text-slate-400 transition"
                title={dark ? 'Switch to light mode' : 'Switch to dark mode'}
                aria-label={dark ? 'Switch to light mode' : 'Switch to dark mode'}
            >
                {dark ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
            </button>

            <div className="w-full max-w-[400px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-8 shadow-card dark:shadow-card-dark">
                <div className="flex justify-center">
                    <WesolutionsLogo />
                </div>
                <h2 className="mt-6 text-center text-xl font-semibold text-slate-900 dark:text-slate-100">Sign in</h2>
                <p className="mt-1 text-center text-sm text-slate-500 dark:text-slate-400">Wesolutions</p>
                <form className="mt-8 space-y-5" onSubmit={handleSubmit}>
                    {error && (
                        <div className="rounded-md border border-rose-200 dark:border-rose-800 bg-rose-50 dark:bg-rose-900/20 px-3 py-2 text-sm text-center text-rose-700 dark:text-rose-400">
                            {error}
                        </div>
                    )}
                    <Input
                        type="email"
                        required
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        placeholder="Email address"
                        label="Email"
                    />
                    <Input
                        type="password"
                        required
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        placeholder="Password"
                        label="Password"
                    />
                    <Button type="submit" className="w-full" variant="primary" size="lg">
                        Sign in
                    </Button>
                    <div className="relative my-4">
                        <div className="absolute inset-0 flex items-center">
                            <div className="w-full border-t border-slate-200 dark:border-slate-700" />
                        </div>
                        <div className="relative flex justify-center text-sm">
                            <span className="px-2 bg-white dark:bg-slate-900 text-slate-500 dark:text-slate-400">or</span>
                        </div>
                    </div>
                    <a
                        href={`${(import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1').replace('/api/v1', '')}/api/v1/auth/google/?next=/&origin=${encodeURIComponent(window.location.origin)}`}
                        className="flex items-center justify-center gap-2 w-full py-3 px-4 rounded-md border border-slate-200 dark:border-slate-600 bg-slate-50 dark:bg-slate-800 font-medium text-sm text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 transition"
                    >
                        <svg className="h-5 w-5" viewBox="0 0 24 24">
                            <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
                            <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                            <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
                            <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
                        </svg>
                        Sign in with Google
                    </a>
                    <p className="text-center text-sm text-slate-500 dark:text-slate-400">
                        Don&apos;t have an account?{' '}
                        <Link to="/register" className="font-medium text-accent-600 dark:text-accent-400 hover:underline">
                            Create account
                        </Link>
                    </p>
                </form>
            </div>
        </div>
    );
};

export default Login;
