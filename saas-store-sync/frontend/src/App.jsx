import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import { ThemeProvider } from './context/ThemeContext';
import PrivateRoute from './components/layout/PrivateRoute';
import DashboardLayout from './layouts/DashboardLayout';
import Login from './pages/Login/Login';
import Register from './pages/Register/Register';
import AuthCallback from './pages/AuthCallback/AuthCallback';
import Dashboard from './pages/Dashboard/Dashboard';
import StoreSettings from './pages/StoreSettings/StoreSettings';

const Catalog = lazy(() => import('./pages/Catalog/Catalog'));
const Orders = lazy(() => import('./pages/Orders/Orders'));

function App() {
    return (
        <ThemeProvider>
            <AuthProvider>
                <BrowserRouter>
                    <Routes>
                        <Route path="/login" element={<Login />} />
                        <Route path="/register" element={<Register />} />
                        <Route path="/auth/callback" element={<AuthCallback />} />

                        <Route element={<PrivateRoute />}>
                            <Route element={<DashboardLayout />}>
                                <Route path="/" element={<Dashboard />} />
                                <Route path="/dashboard" element={<Dashboard />} />
                                <Route path="/store-settings" element={<StoreSettings />} />
                                <Route
                                    path="/catalog"
                                    element={
                                        <Suspense fallback={<div className="p-8 text-slate-600 dark:text-slate-400">Loading catalog…</div>}>
                                            <Catalog />
                                        </Suspense>
                                    }
                                />
                                <Route
                                    path="/orders"
                                    element={
                                        <Suspense fallback={<div className="p-8 text-slate-600 dark:text-slate-400">Loading orders…</div>}>
                                            <Orders />
                                        </Suspense>
                                    }
                                />
                            </Route>
                        </Route>

                        <Route path="*" element={<Navigate to="/" replace />} />
                    </Routes>
                </BrowserRouter>
            </AuthProvider>
        </ThemeProvider>
    );
}

export default App;
