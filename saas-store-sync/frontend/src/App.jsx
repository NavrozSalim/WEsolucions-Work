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
import Catalog from './pages/Catalog/Catalog';

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
                                <Route path="/store-settings" element={<StoreSettings />} />
                                <Route path="/catalog" element={<Catalog />} />
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
