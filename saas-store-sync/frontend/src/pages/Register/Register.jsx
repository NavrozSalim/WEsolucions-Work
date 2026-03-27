import { useState, useContext } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ThemeContext } from '../../context/ThemeContext';
import { WesolutionsLogo } from '../../components/brand';
import { Sun, Moon } from 'lucide-react';
import { register as apiRegister } from '../../services/authService';
import Input from '../../components/ui/Input';
import Button from '../../components/ui/Button';

const Register = () => {
    const { dark, toggleTheme } = useContext(ThemeContext);
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [firstName, setFirstName] = useState('');
    const [lastName, setLastName] = useState('');
    const [error, setError] = useState('');
    const [success, setSuccess] = useState('');
    const navigate = useNavigate();

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');
        setSuccess('');
        try {
            await apiRegister(email, password, firstName, lastName);
            setSuccess('Account created. Redirecting to sign in…');
            setTimeout(() => navigate('/login'), 1500);
        } catch (err) {
            setError(err.response?.data?.email?.[0] || err.response?.data?.password?.[0] || 'Registration failed. Try again.');
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
                <h2 className="mt-6 text-center text-xl font-semibold text-slate-900 dark:text-slate-100">Create account</h2>
                <p className="mt-1 text-center text-sm text-slate-500 dark:text-slate-400">Wesolutions</p>
                <form className="mt-8 space-y-5" onSubmit={handleSubmit}>
                    {error && (
                        <div className="rounded-md border border-rose-200 dark:border-rose-800 bg-rose-50 dark:bg-rose-900/20 px-3 py-2 text-sm text-center text-rose-700 dark:text-rose-400">
                            {error}
                        </div>
                    )}
                    {success && (
                        <div className="rounded-md border border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-900/20 px-3 py-2 text-sm text-center text-emerald-700 dark:text-emerald-400">
                            {success}
                        </div>
                    )}
                    <div className="grid grid-cols-2 gap-3">
                        <Input
                            type="text"
                            value={firstName}
                            onChange={(e) => setFirstName(e.target.value)}
                            placeholder="First name"
                            label="First name"
                        />
                        <Input
                            type="text"
                            value={lastName}
                            onChange={(e) => setLastName(e.target.value)}
                            placeholder="Last name"
                            label="Last name"
                        />
                    </div>
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
                        Create account
                    </Button>
                    <p className="text-center text-sm text-slate-500 dark:text-slate-400">
                        Already have an account?{' '}
                        <Link to="/login" className="font-medium text-accent-600 dark:text-accent-400 hover:underline">
                            Sign in
                        </Link>
                    </p>
                </form>
            </div>
        </div>
    );
};

export default Register;
