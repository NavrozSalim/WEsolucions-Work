import { useState, useContext, useEffect, useCallback } from 'react';
import { Link, useLocation, Outlet } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { AuthContext } from '../context/AuthContext';
import { ThemeContext } from '../context/ThemeContext';
import {
    LayoutDashboard,
    Package,
    LogOut,
    Menu,
    X,
    ChevronDown,
    User,
    Store,
    Sun,
    Moon,
} from 'lucide-react';
import { WesolutionsLogo } from '../components/brand';
import { SidebarActivityProvider, useSidebarActivity } from '../context/SidebarActivityContext';
import SidebarActivityPanel from '../components/layout/SidebarActivityPanel';

const navItems = [
    { path: '/', label: 'Dashboard', icon: LayoutDashboard },
    { path: '/store-settings', label: 'Stores', icon: Store },
    { path: '/catalog', label: 'Catalog', icon: Package },
];

function DashboardLayoutInner() {
    const { user, logout } = useContext(AuthContext);
    const { dark, toggleTheme } = useContext(ThemeContext);
    const location = useLocation();
    const { activities } = useSidebarActivity();

    // Mobile: drawer open/closed. Desktop: sidebar always visible, but can collapse.
    const [mobileOpen, setMobileOpen] = useState(false);
    const [desktopCollapsed, setDesktopCollapsed] = useState(false);
    const [userMenuOpen, setUserMenuOpen] = useState(false);

    const closeMobileSidebar = useCallback(() => setMobileOpen(false), []);

    // ESC to close mobile sidebar
    useEffect(() => {
        const handleKeyDown = (e) => {
            if (e.key === 'Escape') setMobileOpen(false);
        };
        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, []);

    // Prevent body scroll when mobile sidebar open
    useEffect(() => {
        if (mobileOpen) {
            document.body.style.overflow = 'hidden';
        }
        return () => {
            document.body.style.overflow = '';
        };
    }, [mobileOpen]);

    // Detect desktop for correct hamburger behavior
    const [isDesktop, setIsDesktop] = useState(false);
    useEffect(() => {
        const mq = window.matchMedia('(min-width: 1024px)');
        const handler = () => setIsDesktop(mq.matches);
        handler();
        mq.addEventListener('change', handler);
        return () => mq.removeEventListener('change', handler);
    }, []);

    const handleMenuToggle = () => {
        if (isDesktop) {
            setDesktopCollapsed((c) => !c);
        } else {
            setMobileOpen(true);
        }
    };

    return (
        <div
            className={`min-h-screen ${
                dark ? 'bg-slate-950 text-slate-100' : 'bg-slate-50 text-slate-900'
            }`}
        >
            {/* Mobile overlay — dark backdrop when sidebar open */}
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

            {/* Sidebar — desktop: always visible, collapsible. Mobile: off-canvas drawer */}
            <aside
                className={`fixed top-0 left-0 z-50 h-full flex flex-col border-r transition-[width,transform] duration-200 ease-in-out
                    ${dark ? 'border-slate-800 bg-slate-950' : 'border-slate-200 bg-white'}
                    lg:translate-x-0
                    ${mobileOpen ? 'translate-x-0' : '-translate-x-full'}
                    ${desktopCollapsed ? 'lg:w-20' : 'lg:w-64'}
                    w-64
                `}
            >
                {/* Sidebar header — logo + close/toggle */}
                <div
                    className={`flex h-14 shrink-0 items-center justify-between border-b px-3 ${
                        dark ? 'border-slate-800' : 'border-slate-200'
                    } ${desktopCollapsed ? 'lg:justify-center lg:px-0' : ''}`}
                >
                    <Link
                        to="/"
                        className={`flex items-center overflow-hidden ${desktopCollapsed ? 'lg:justify-center lg:w-full' : 'min-w-0 flex-1'}`}
                        onClick={closeMobileSidebar}
                    >
                        {desktopCollapsed ? (
                            <WesolutionsLogo iconOnly />
                        ) : (
                            <WesolutionsLogo compact />
                        )}
                    </Link>
                    <button
                        type="button"
                        className={`lg:hidden shrink-0 p-2 rounded-md ${dark ? 'hover:bg-slate-800 text-slate-400' : 'hover:bg-slate-100 text-slate-600'}`}
                        onClick={closeMobileSidebar}
                        aria-label="Close menu"
                    >
                        <X className="h-5 w-5" />
                    </button>
                </div>

                <nav className="flex min-h-0 flex-1 flex-col overflow-hidden px-3 py-4">
                    <div className="shrink-0 space-y-0.5 overflow-x-hidden overflow-y-auto">
                        {navItems.map(({ path, label, icon: Icon }) => {
                            const isActive =
                                location.pathname === path ||
                                (path !== '/' && location.pathname.startsWith(path));
                            return (
                                <Link
                                    key={path}
                                    to={path}
                                    title={desktopCollapsed ? label : undefined}
                                    className={`flex items-center gap-3 rounded-md text-sm font-medium transition-colors ${
                                        desktopCollapsed ? 'lg:justify-center lg:px-0' : 'px-3 py-2'
                                    } py-2 ${
                                        isActive
                                            ? dark
                                                ? 'bg-slate-800 text-slate-100'
                                                : 'bg-slate-100 text-slate-900'
                                            : dark
                                              ? 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'
                                              : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
                                    }`}
                                    onClick={closeMobileSidebar}
                                >
                                    <Icon className="h-5 w-5 shrink-0 opacity-80" />
                                    {!desktopCollapsed && <span>{label}</span>}
                                </Link>
                            );
                        })}
                    </div>
                    <SidebarActivityPanel activities={activities} desktopCollapsed={desktopCollapsed} />
                </nav>

                <div
                    className={`shrink-0 border-t p-3 ${dark ? 'border-slate-800' : 'border-slate-200'}`}
                >
                    <button
                        type="button"
                        onClick={toggleTheme}
                        className={`flex w-full items-center gap-3 rounded-md text-sm font-medium transition-colors ${
                            desktopCollapsed ? 'lg:justify-center lg:px-0' : 'px-3 py-2'
                        } py-2 ${
                            dark
                                ? 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'
                                : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
                        }`}
                        aria-label={dark ? 'Switch to light mode' : 'Switch to dark mode'}
                    >
                        {dark ? <Sun className="h-5 w-5 shrink-0" /> : <Moon className="h-5 w-5 shrink-0" />}
                        {!desktopCollapsed && (
                            <span>{dark ? 'Light mode' : 'Dark mode'}</span>
                        )}
                    </button>
                </div>
            </aside>

            {/* Main content — padding matches sidebar width on desktop */}
            <div
                className={`min-h-screen pl-0 transition-[padding] duration-200 ease-in-out ${
                    desktopCollapsed ? 'lg:pl-20' : 'lg:pl-64'
                }`}
            >
                {/* Navbar */}
                <header
                    className={`sticky top-0 z-30 flex h-14 shrink-0 items-center gap-4 border-b px-4 lg:px-6 ${
                        dark
                            ? 'border-slate-800 bg-slate-950/95 backdrop-blur'
                            : 'border-slate-200 bg-white/95 backdrop-blur'
                    }`}
                >
                    <button
                        type="button"
                        className={`p-2 rounded-md transition-colors ${
                            dark ? 'hover:bg-slate-800 text-slate-400' : 'hover:bg-slate-100 text-slate-600'
                        }`}
                        onClick={handleMenuToggle}
                        aria-label={isDesktop ? (desktopCollapsed ? 'Expand sidebar' : 'Collapse sidebar') : 'Open menu'}
                        aria-expanded={isDesktop ? !desktopCollapsed : mobileOpen}
                    >
                        <Menu className="h-5 w-5" />
                    </button>

                    <div className="flex-1 min-w-0" />
                    <div className="flex items-center gap-1">
                    <button
                        type="button"
                        onClick={toggleTheme}
                        className={`p-2 rounded-md transition-colors ${
                            dark ? 'hover:bg-slate-800 text-slate-400' : 'hover:bg-slate-100 text-slate-600'
                        }`}
                        aria-label={dark ? 'Switch to light mode' : 'Switch to dark mode'}
                    >
                        {dark ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
                    </button>
                    <div className="relative">
                            <button
                                type="button"
                                className={`flex items-center gap-2 px-2 py-1.5 rounded-md transition-colors ${
                                    dark ? 'hover:bg-slate-800' : 'hover:bg-slate-100'
                                }`}
                                onClick={() => setUserMenuOpen((o) => !o)}
                            >
                                <div
                                    className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md ${
                                        dark ? 'bg-slate-800' : 'bg-slate-100'
                                    }`}
                                >
                                    <User className="h-4 w-4 text-slate-500" />
                                </div>
                                <span
                                    className={`hidden sm:block text-sm font-medium truncate max-w-[140px] ${
                                        dark ? 'text-slate-300' : 'text-slate-700'
                                    }`}
                                >
                                    {user?.email}
                                </span>
                                <ChevronDown className="h-4 w-4 text-slate-400 shrink-0" />
                            </button>
                            {userMenuOpen && (
                                <>
                                    <div
                                        className="fixed inset-0 z-10"
                                        onClick={() => setUserMenuOpen(false)}
                                        aria-hidden="true"
                                    />
                                    <div
                                        className={`absolute right-0 mt-1 w-56 z-20 rounded-lg border py-1 shadow-dropdown ${
                                            dark
                                                ? 'border-slate-700 bg-slate-900'
                                                : 'border-slate-200 bg-white'
                                        }`}
                                    >
                                        <div
                                            className={`px-4 py-2 border-b ${
                                                dark ? 'border-slate-700' : 'border-slate-100'
                                            }`}
                                        >
                                            <p
                                                className={`text-sm font-medium truncate ${
                                                    dark ? 'text-slate-100' : 'text-slate-900'
                                                }`}
                                            >
                                                {user?.email}
                                            </p>
                                            <p className="text-xs text-slate-500">Account</p>
                                        </div>
                                        <button
                                            type="button"
                                            className={`flex w-full items-center gap-2 px-4 py-2 text-sm text-rose-600 dark:text-rose-400 hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-colors`}
                                            onClick={() => {
                                                setUserMenuOpen(false);
                                                logout();
                                            }}
                                        >
                                            <LogOut className="h-4 w-4 shrink-0" />
                                            Log out
                                        </button>
                                    </div>
                                </>
                            )}
                    </div>
                    </div>
                </header>

                <main className="p-4 lg:p-6">
                    <Outlet />
                </main>
            </div>
        </div>
    );
}

export default function DashboardLayout() {
    return (
        <SidebarActivityProvider>
            <DashboardLayoutInner />
        </SidebarActivityProvider>
    );
}
