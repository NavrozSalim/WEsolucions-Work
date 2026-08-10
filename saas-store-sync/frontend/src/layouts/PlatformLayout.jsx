import { useCallback, useContext, useEffect, useState } from 'react';
import { Link, Outlet, useNavigate, useSearchParams } from 'react-router-dom';
import { AnimatePresence, motion } from 'framer-motion';
import {
    Building2,
    ChevronDown,
    LayoutDashboard,
    LogOut,
    Menu,
    Moon,
    ScrollText,
    Shield,
    Sun,
    Users,
    X,
} from 'lucide-react';
import { AuthContext } from '../context/AuthContext';
import { ThemeContext } from '../context/ThemeContext';
import { SellerPilotHubLogo } from '../components/brand';

const NAV = [
    { id: 'overview', label: 'Overview', icon: LayoutDashboard },
    { id: 'users', label: 'Users', icon: Users },
    { id: 'organizations', label: 'Organizations', icon: Building2 },
    { id: 'audit', label: 'Audit log', icon: ScrollText },
];

export default function PlatformLayout() {
    const { user, logout } = useContext(AuthContext);
    const { dark, toggleTheme } = useContext(ThemeContext);
    const navigate = useNavigate();
    const [searchParams] = useSearchParams();
    const activeView = searchParams.get('view') || 'overview';

    const [mobileOpen, setMobileOpen] = useState(false);
    const [desktopCollapsed, setDesktopCollapsed] = useState(false);
    const [userMenuOpen, setUserMenuOpen] = useState(false);
    const [isDesktop, setIsDesktop] = useState(false);

    const closeMobileSidebar = useCallback(() => setMobileOpen(false), []);

    useEffect(() => {
        const handleKeyDown = (e) => {
            if (e.key === 'Escape') setMobileOpen(false);
        };
        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, []);

    useEffect(() => {
        if (mobileOpen) document.body.style.overflow = 'hidden';
        return () => {
            document.body.style.overflow = '';
        };
    }, [mobileOpen]);

    useEffect(() => {
        const mq = window.matchMedia('(min-width: 1024px)');
        const handler = () => setIsDesktop(mq.matches);
        handler();
        mq.addEventListener('change', handler);
        return () => mq.removeEventListener('change', handler);
    }, []);

    const handleMenuToggle = () => {
        if (isDesktop) setDesktopCollapsed((c) => !c);
        else setMobileOpen(true);
    };

    const handleSignOut = () => {
        setUserMenuOpen(false);
        logout();
        navigate('/login/master', { replace: true });
    };

    const shellBg = dark ? 'bg-slate-950 text-slate-100' : 'bg-slate-50 text-slate-900';
    const asideBg = dark ? 'border-slate-800 bg-slate-950' : 'border-slate-200 bg-white';
    const headerBg = dark
        ? 'border-slate-800 bg-slate-950/95 backdrop-blur'
        : 'border-slate-200 bg-white/95 backdrop-blur';

    return (
        <div className={`min-h-screen ${shellBg}`}>
            <AnimatePresence>
                {mobileOpen && (
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.2 }}
                        className="fixed inset-0 z-40 bg-black/50 lg:hidden"
                        onClick={closeMobileSidebar}
                        aria-hidden="true"
                    />
                )}
            </AnimatePresence>

            <aside
                className={`fixed top-0 left-0 z-50 flex h-full w-64 flex-col border-r transition-[width,transform] duration-200 ease-in-out
                    ${asideBg}
                    lg:translate-x-0
                    ${mobileOpen ? 'translate-x-0' : '-translate-x-full'}
                    ${desktopCollapsed ? 'lg:w-20' : 'lg:w-64'}
                `}
            >
                <div
                    className={`flex h-14 shrink-0 items-center justify-between border-b px-3 ${
                        dark ? 'border-slate-800' : 'border-slate-200'
                    } ${desktopCollapsed ? 'lg:justify-center lg:px-0' : ''}`}
                >
                    <Link
                        to="/platform"
                        className={`flex items-center overflow-hidden ${
                            desktopCollapsed ? 'lg:w-full lg:justify-center' : 'min-w-0 flex-1'
                        }`}
                        onClick={closeMobileSidebar}
                    >
                        {desktopCollapsed ? (
                            <SellerPilotHubLogo iconOnly />
                        ) : (
                            <SellerPilotHubLogo compact />
                        )}
                    </Link>
                    <button
                        type="button"
                        className={`shrink-0 rounded-md p-2 lg:hidden ${
                            dark ? 'text-slate-400 hover:bg-slate-800' : 'text-slate-600 hover:bg-slate-100'
                        }`}
                        onClick={closeMobileSidebar}
                        aria-label="Close menu"
                    >
                        <X className="h-5 w-5" />
                    </button>
                </div>

                <div className={`px-3 pt-4 ${desktopCollapsed ? 'lg:px-2' : ''}`}>
                    <div
                        className={`flex items-center gap-2 rounded-md px-3 py-2 ${
                            dark ? 'bg-slate-900/80' : 'bg-slate-100'
                        } ${desktopCollapsed ? 'lg:justify-center lg:px-0' : ''}`}
                    >
                        <Shield className="h-4 w-4 shrink-0 text-accent-500" />
                        {!desktopCollapsed && (
                            <div className="min-w-0">
                                <p className="truncate text-xs font-semibold text-slate-900 dark:text-slate-100">
                                    Platform admin
                                </p>
                                <p className="truncate text-[11px] text-slate-500">Master controls</p>
                            </div>
                        )}
                    </div>
                </div>

                <nav className="flex min-h-0 flex-1 flex-col overflow-hidden px-3 py-4">
                    <div className="shrink-0 space-y-0.5 overflow-y-auto overflow-x-hidden">
                        {NAV.map(({ id, label, icon: Icon }) => {
                            const isActive = activeView === id;
                            return (
                                <Link
                                    key={id}
                                    to={id === 'overview' ? '/platform' : `/platform?view=${id}`}
                                    title={desktopCollapsed ? label : undefined}
                                    className={`flex items-center gap-3 rounded-md py-2 text-sm font-medium transition-colors ${
                                        desktopCollapsed ? 'lg:justify-center lg:px-0' : 'px-3'
                                    } ${
                                        isActive
                                            ? dark
                                                ? 'bg-slate-800 text-slate-100'
                                                : 'bg-slate-100 text-slate-900'
                                            : dark
                                              ? 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'
                                              : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
                                    }`}
                                    onClick={closeMobileSidebar}
                                    aria-current={isActive ? 'page' : undefined}
                                >
                                    <Icon className="h-5 w-5 shrink-0 opacity-80" />
                                    {!desktopCollapsed && <span>{label}</span>}
                                </Link>
                            );
                        })}
                    </div>
                </nav>

                <div
                    className={`shrink-0 border-t p-3 ${dark ? 'border-slate-800' : 'border-slate-200'}`}
                >
                    <button
                        type="button"
                        onClick={toggleTheme}
                        className={`flex w-full items-center gap-3 rounded-md py-2 text-sm font-medium transition-colors ${
                            desktopCollapsed ? 'lg:justify-center lg:px-0' : 'px-3'
                        } ${
                            dark
                                ? 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'
                                : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
                        }`}
                        aria-label={dark ? 'Switch to light mode' : 'Switch to dark mode'}
                    >
                        {dark ? <Sun className="h-5 w-5 shrink-0" /> : <Moon className="h-5 w-5 shrink-0" />}
                        {!desktopCollapsed && <span>{dark ? 'Light mode' : 'Dark mode'}</span>}
                    </button>
                </div>
            </aside>

            <div
                className={`min-h-screen pl-0 transition-[padding] duration-200 ease-in-out ${
                    desktopCollapsed ? 'lg:pl-20' : 'lg:pl-64'
                }`}
            >
                <header className={`sticky top-0 z-30 flex h-14 shrink-0 items-center gap-4 border-b px-4 lg:px-6 ${headerBg}`}>
                    <button
                        type="button"
                        className={`rounded-md p-2 transition-colors ${
                            dark ? 'text-slate-400 hover:bg-slate-800' : 'text-slate-600 hover:bg-slate-100'
                        }`}
                        onClick={handleMenuToggle}
                        aria-label={
                            isDesktop
                                ? desktopCollapsed
                                    ? 'Expand sidebar'
                                    : 'Collapse sidebar'
                                : 'Open menu'
                        }
                        aria-expanded={isDesktop ? !desktopCollapsed : mobileOpen}
                    >
                        <Menu className="h-5 w-5" />
                    </button>

                    <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium text-slate-700 dark:text-slate-200">
                            {NAV.find((n) => n.id === activeView)?.label || 'Overview'}
                        </p>
                    </div>

                    <div className="flex items-center gap-1">
                        <button
                            type="button"
                            onClick={toggleTheme}
                            className={`rounded-md p-2 transition-colors ${
                                dark ? 'text-slate-400 hover:bg-slate-800' : 'text-slate-600 hover:bg-slate-100'
                            }`}
                            aria-label={dark ? 'Switch to light mode' : 'Switch to dark mode'}
                        >
                            {dark ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
                        </button>

                        <div className="relative">
                            <button
                                type="button"
                                className={`flex items-center gap-2 rounded-md px-2 py-1.5 transition-colors ${
                                    dark ? 'hover:bg-slate-800' : 'hover:bg-slate-100'
                                }`}
                                onClick={() => setUserMenuOpen((o) => !o)}
                            >
                                <div
                                    className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md ${
                                        dark ? 'bg-slate-800' : 'bg-slate-100'
                                    }`}
                                >
                                    <Shield className="h-4 w-4 text-accent-500" />
                                </div>
                                <span
                                    className={`hidden max-w-[160px] truncate text-sm font-medium sm:block ${
                                        dark ? 'text-slate-300' : 'text-slate-700'
                                    }`}
                                >
                                    {user?.email}
                                </span>
                                <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" />
                            </button>
                            {userMenuOpen && (
                                <>
                                    <div
                                        className="fixed inset-0 z-10"
                                        onClick={() => setUserMenuOpen(false)}
                                        aria-hidden="true"
                                    />
                                    <div
                                        className={`absolute right-0 z-20 mt-1 w-56 rounded-lg border py-1 shadow-dropdown ${
                                            dark
                                                ? 'border-slate-700 bg-slate-900'
                                                : 'border-slate-200 bg-white'
                                        }`}
                                    >
                                        <div
                                            className={`border-b px-4 py-2 ${
                                                dark ? 'border-slate-700' : 'border-slate-100'
                                            }`}
                                        >
                                            <p
                                                className={`truncate text-sm font-medium ${
                                                    dark ? 'text-slate-100' : 'text-slate-900'
                                                }`}
                                            >
                                                {user?.email}
                                            </p>
                                            <p className="text-xs text-slate-500">Platform administrator</p>
                                        </div>
                                        <button
                                            type="button"
                                            className="flex w-full items-center gap-2 px-4 py-2 text-sm text-rose-600 transition-colors hover:bg-slate-50 dark:text-rose-400 dark:hover:bg-slate-800/50"
                                            onClick={handleSignOut}
                                        >
                                            <LogOut className="h-4 w-4 shrink-0" />
                                            Sign out
                                        </button>
                                    </div>
                                </>
                            )}
                        </div>
                    </div>
                </header>

                <main className="overflow-x-hidden p-3 sm:p-4 lg:p-6">
                    <Outlet />
                </main>
            </div>
        </div>
    );
}
