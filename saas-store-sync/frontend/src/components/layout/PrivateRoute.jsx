import { Navigate, Outlet } from 'react-router-dom';
import { useContext } from 'react';
import { AuthContext } from '../../context/AuthContext';
import { SellerPilotHubLoading } from '../brand';

const PrivateRoute = () => {
    const { user, loading } = useContext(AuthContext);

    if (loading) return <SellerPilotHubLoading />;

    if (!user) return <Navigate to="/login/choose" replace />;

    return <Outlet />;
};

export default PrivateRoute;
