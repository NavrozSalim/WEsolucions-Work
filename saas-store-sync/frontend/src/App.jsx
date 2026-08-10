import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import { ThemeProvider } from './context/ThemeContext';
import { I18nProvider } from './context/I18nContext';
import PrivateRoute from './components/layout/PrivateRoute';
import PermissionRoute from './components/layout/PermissionRoute';
import PlatformRoute from './components/layout/PlatformRoute';
import DashboardLayout from './layouts/DashboardLayout';
import PlatformLayout from './layouts/PlatformLayout';
import Landing from './pages/Landing/Landing';
import Login from './pages/Login/Login';
import LoginChooser from './pages/Login/LoginChooser';
import SuperUserSignup from './pages/Signup/SuperUserSignup';
import AuthCallback from './pages/AuthCallback/AuthCallback';
import Dashboard from './pages/Dashboard/Dashboard';
import StoreSettings from './pages/StoreSettings/StoreSettings';
import Team from './pages/Team/Team';
import PlatformAdmin from './pages/Platform/PlatformAdmin';

const Catalog = lazy(() => import('./pages/Catalog/Catalog'));
const Orders = lazy(() => import('./pages/Orders/Orders'));
const Tickets = lazy(() => import('./pages/Tickets/Tickets'));

function App() {
    return (
        <ThemeProvider>
            <I18nProvider>
                <AuthProvider>
                    <BrowserRouter>
                        <Routes>
                            <Route path="/" element={<Landing />} />
                            <Route path="/pricing" element={<Navigate to="/signup/super" replace />} />
                            <Route path="/signup/super" element={<SuperUserSignup />} />
                            <Route path="/login" element={<Navigate to="/login/choose" replace />} />
                            <Route path="/login/choose" element={<LoginChooser />} />
                            <Route path="/login/:mode" element={<Login />} />
                            <Route path="/register" element={<Navigate to="/signup/super" replace />} />
                            <Route path="/auth/callback" element={<AuthCallback />} />

                            <Route element={<PlatformRoute />}>
                                <Route element={<PlatformLayout />}>
                                    <Route path="/platform" element={<PlatformAdmin />} />
                                </Route>
                            </Route>

                            <Route element={<PrivateRoute />}>
                                <Route element={<DashboardLayout />}>
                                    <Route element={<PermissionRoute permission="dashboard" />}>
                                        <Route path="/app" element={<Dashboard />} />
                                        <Route path="/dashboard" element={<Dashboard />} />
                                    </Route>
                                    <Route element={<PermissionRoute permission="stores" />}>
                                        <Route path="/store-settings" element={<StoreSettings />} />
                                    </Route>
                                    <Route element={<PermissionRoute permission="catalog" />}>
                                        <Route
                                            path="/catalog"
                                            element={
                                                <Suspense
                                                    fallback={
                                                        <div className="p-8 text-slate-600 dark:text-slate-400">
                                                            Loading catalog…
                                                        </div>
                                                    }
                                                >
                                                    <Catalog />
                                                </Suspense>
                                            }
                                        />
                                    </Route>
                                    <Route element={<PermissionRoute permission="orders" />}>
                                        <Route
                                            path="/orders"
                                            element={
                                                <Suspense
                                                    fallback={
                                                        <div className="p-8 text-slate-600 dark:text-slate-400">
                                                            Loading orders…
                                                        </div>
                                                    }
                                                >
                                                    <Orders />
                                                </Suspense>
                                            }
                                        />
                                    </Route>
                                    <Route element={<PermissionRoute permission="tickets" />}>
                                        <Route
                                            path="/tickets"
                                            element={
                                                <Suspense
                                                    fallback={
                                                        <div className="p-8 text-slate-600 dark:text-slate-400">
                                                            Loading tickets…
                                                        </div>
                                                    }
                                                >
                                                    <Tickets />
                                                </Suspense>
                                            }
                                        />
                                    </Route>
                                    <Route element={<PermissionRoute permission="team" />}>
                                        <Route path="/team" element={<Team />} />
                                    </Route>
                                </Route>
                            </Route>

                            <Route path="*" element={<Navigate to="/" replace />} />
                        </Routes>
                    </BrowserRouter>
                </AuthProvider>
            </I18nProvider>
        </ThemeProvider>
    );
}

export default App;
