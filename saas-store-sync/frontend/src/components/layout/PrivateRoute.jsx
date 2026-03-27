import { Navigate, Outlet } from 'react-router-dom';
import { useContext } from 'react';
import { AuthContext } from '../../context/AuthContext';
import { WesolutionsLoading } from '../brand';

const PrivateRoute = () => {
    const { user, loading } = useContext(AuthContext);

    if (loading) return <WesolutionsLoading />;

    if (!user) return <Navigate to="/login" replace />;

    return <Outlet />;
};

export default PrivateRoute;
