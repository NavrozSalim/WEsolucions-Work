import { useCallback, useContext, useEffect, useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { AuthContext } from '../../context/AuthContext';
import {
    getPlatformAudit,
    getPlatformOrganizations,
    getPlatformStats,
    getPlatformUserDetail,
    getPlatformUsers,
    removePlatformUser,
    updatePlatformUser,
} from '../../services/authService';
import Button from '../../components/ui/Button';
import Input from '../../components/ui/Input';

function formatWhen(iso) {
    if (!iso) return '—';
    try {
        return new Date(iso).toLocaleString();
    } catch {
        return iso;
    }
}

export default function PlatformAdmin() {
    const { user, logout, loading } = useContext(AuthContext);
    const navigate = useNavigate();
    const [stats, setStats] = useState(null);
    const [viewMode, setViewMode] = useState('users');
    const [users, setUsers] = useState([]);
    const [organizations, setOrganizations] = useState([]);
    const [auditEvents, setAuditEvents] = useState([]);
    const [filter, setFilter] = useState('');
    const [accountType, setAccountType] = useState('');
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const [userTotal, setUserTotal] = useState(0);
    const [userOffset, setUserOffset] = useState(0);
    const [orgTotal, setOrgTotal] = useState(0);
    const [orgOffset, setOrgOffset] = useState(0);
    const pageSize = 50;
    const [selected, setSelected] = useState(null);
    const [events, setEvents] = useState([]);

    const loadUsers = useCallback(async () => {
        setError('');
        const list = await getPlatformUsers({
            account_type: accountType || undefined,
            q: filter || undefined,
            limit: pageSize,
            offset: userOffset,
        });
        setUsers(list.users || []);
        setUserTotal(list.total || 0);
    }, [accountType, filter, userOffset]);

    const loadOrganizations = useCallback(async () => {
        setError('');
        const list = await getPlatformOrganizations({
            q: filter || undefined,
            limit: pageSize,
            offset: orgOffset,
        });
        setOrganizations(list.organizations || []);
        setOrgTotal(list.total || 0);
    }, [filter, orgOffset]);

    const load = useCallback(async () => {
        setError('');
        setBusy(true);
        try {
            const [s, audits] = await Promise.all([
                getPlatformStats(),
                getPlatformAudit({ limit: 20, offset: 0 }),
            ]);
            setStats(s);
            setAuditEvents(audits.events || []);
            if (viewMode === 'users') {
                await loadUsers();
            } else {
                await loadOrganizations();
            }
        } catch (err) {
            setError(err.response?.data?.detail || 'Could not load platform data.');
        } finally {
            setBusy(false);
        }
    }, [loadOrganizations, loadUsers, viewMode]);

    useEffect(() => {
        if (user?.is_platform_admin) load();
    }, [user, load]);

    const openDetail = async (row) => {
        setBusy(true);
        setError('');
        try {
            const data = await getPlatformUserDetail(row.id);
            setSelected(data.user);
            setEvents(data.login_events || []);
        } catch (err) {
            setError(err.response?.data?.detail || 'Could not load login history.');
        } finally {
            setBusy(false);
        }
    };

    const setUserActive = async (row, isActive) => {
        const label = isActive ? `Reactivate ${row.email}?` : `Deactivate ${row.email}?`;
        if (!window.confirm(label)) return;
        setBusy(true);
        setError('');
        try {
            await updatePlatformUser(row.id, { is_active: isActive });
            if (selected?.id === row.id) {
                await openDetail(row);
            }
            await load();
        } catch (err) {
            setError(err.response?.data?.detail || 'Update failed.');
        } finally {
            setBusy(false);
        }
    };

    const removeUser = async (row, hard = false) => {
        const label = hard ? `Permanently delete ${row.email}?` : `Deactivate ${row.email}?`;
        if (!window.confirm(label)) return;
        setBusy(true);
        setError('');
        try {
            await removePlatformUser(row.id, { hard });
            if (selected?.id === row.id) {
                setSelected(null);
                setEvents([]);
            }
            await load();
        } catch (err) {
            setError(err.response?.data?.detail || 'Remove failed.');
        } finally {
            setBusy(false);
        }
    };

    const userFrom = users.length ? userOffset + 1 : 0;
    const userTo = userOffset + users.length;
    const orgFrom = organizations.length ? orgOffset + 1 : 0;
    const orgTo = orgOffset + organizations.length;

    if (loading) {
        return <div className="p-8 text-slate-500">Loading…</div>;
    }
    if (!user?.is_platform_admin) {
        return <Navigate to="/login/master" replace />;
    }

    return (
        <div className="min-h-screen bg-slate-50 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
            <header className="border-b border-slate-200 bg-white px-6 py-4 dark:border-slate-800 dark:bg-slate-900">
                <div className="mx-auto flex max-w-6xl items-center justify-between gap-4">
                    <div>
                        <h1 className="text-xl font-semibold">Platform admin</h1>
                        <p className="text-sm text-slate-500">{user.email}</p>
                    </div>
                    <div className="flex gap-2 items-center">
                        <span className="text-xs text-slate-500">Master controls</span>
                        <Button
                            type="button"
                            variant="secondary"
                            onClick={() => {
                                logout();
                                navigate('/login/master', { replace: true });
                            }}
                        >
                            Sign out
                        </Button>
                    </div>
                </div>
            </header>

            <main className="mx-auto max-w-6xl space-y-6 px-6 py-8">
                {error ? (
                    <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:border-rose-800 dark:bg-rose-900/20 dark:text-rose-300">
                        {error}
                    </div>
                ) : null}

                {stats ? (
                    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                        {[
                            ['Super Users', stats.super_users],
                            ['User Accounts', stats.user_accounts],
                            ['Organizations', stats.organizations],
                            ['Logins (24h)', stats.recent_logins_24h],
                            ['Failed (24h)', stats.failed_logins_24h],
                        ].map(([label, value]) => (
                            <div
                                key={label}
                                className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900"
                            >
                                <p className="text-sm text-slate-500">{label}</p>
                                <p className="mt-1 text-2xl font-semibold">{value}</p>
                            </div>
                        ))}
                    </div>
                ) : null}

                <div className="flex gap-2">
                    <button
                        type="button"
                        className={`rounded-md px-3 py-1.5 text-sm ${viewMode === 'users' ? 'bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900' : 'border border-slate-300 text-slate-700 dark:border-slate-700 dark:text-slate-200'}`}
                        onClick={() => {
                            setViewMode('users');
                            setUserOffset(0);
                        }}
                    >
                        Users
                    </button>
                    <button
                        type="button"
                        className={`rounded-md px-3 py-1.5 text-sm ${viewMode === 'organizations' ? 'bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900' : 'border border-slate-300 text-slate-700 dark:border-slate-700 dark:text-slate-200'}`}
                        onClick={() => {
                            setViewMode('organizations');
                            setOrgOffset(0);
                            setSelected(null);
                            setEvents([]);
                        }}
                    >
                        Organizations
                    </button>
                </div>

                <div className="flex flex-wrap items-end gap-3">
                    <div className="min-w-[200px] flex-1">
                        <Input
                            label="Search"
                            value={filter}
                            onChange={(e) => {
                                setFilter(e.target.value);
                                setUserOffset(0);
                                setOrgOffset(0);
                            }}
                            placeholder={viewMode === 'users' ? 'email, name, org' : 'organization or owner email'}
                        />
                    </div>
                    {viewMode === 'users' ? (
                        <label className="text-sm text-slate-600 dark:text-slate-400">
                            Type
                            <select
                                className="mt-1 block rounded-md border border-slate-300 bg-white px-2 py-2 text-sm dark:border-slate-600 dark:bg-slate-800"
                                value={accountType}
                                onChange={(e) => {
                                    setAccountType(e.target.value);
                                    setUserOffset(0);
                                }}
                            >
                                <option value="">All</option>
                                <option value="super_user">Super User</option>
                                <option value="user_account">User Account</option>
                                <option value="standalone">Standalone</option>
                            </select>
                        </label>
                    ) : null}
                    <Button type="button" onClick={load} disabled={busy}>
                        Refresh
                    </Button>
                </div>

                {viewMode === 'users' ? (
                    <div className="grid gap-6 lg:grid-cols-5">
                        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white lg:col-span-3 dark:border-slate-800 dark:bg-slate-900">
                            <table className="w-full text-left text-sm">
                                <thead className="border-b border-slate-200 text-slate-500 dark:border-slate-800">
                                    <tr>
                                        <th className="px-3 py-2 font-medium">Email</th>
                                        <th className="px-3 py-2 font-medium">Type</th>
                                        <th className="px-3 py-2 font-medium">Last login</th>
                                        <th className="px-3 py-2 font-medium">Actions</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {users.length === 0 ? (
                                        <tr>
                                            <td colSpan={4} className="px-3 py-6 text-slate-500">
                                                No accounts found.
                                            </td>
                                        </tr>
                                    ) : (
                                        users.map((row) => (
                                            <tr
                                                key={row.id}
                                                className="border-b border-slate-100 dark:border-slate-800"
                                            >
                                                <td className="px-3 py-2">
                                                    <button
                                                        type="button"
                                                        className="font-medium text-sky-700 hover:underline dark:text-sky-400"
                                                        onClick={() => openDetail(row)}
                                                    >
                                                        {row.email}
                                                    </button>
                                                    {!row.is_active ? (
                                                        <span className="ml-2 text-xs text-rose-500">inactive</span>
                                                    ) : null}
                                                    <div className="text-xs text-slate-500">
                                                        {row.organization?.name || '—'}
                                                    </div>
                                                </td>
                                                <td className="px-3 py-2">{row.account_type}</td>
                                                <td className="px-3 py-2">
                                                    <div>{formatWhen(row.latest_login?.created_at || row.last_login)}</div>
                                                    <div className="text-xs text-slate-500">
                                                        {row.latest_login?.ip_address || '—'} · {row.latest_login?.client?.label || 'Unknown device'}
                                                    </div>
                                                </td>
                                                <td className="px-3 py-2">
                                                    <div className="flex flex-col gap-1">
                                                        <button
                                                            type="button"
                                                            className="text-left text-sky-700 hover:underline dark:text-sky-400"
                                                            onClick={() => openDetail(row)}
                                                        >
                                                            Sessions
                                                        </button>
                                                        <button
                                                            type="button"
                                                            className="text-left text-amber-700 hover:underline dark:text-amber-400"
                                                            disabled={busy}
                                                            onClick={() => setUserActive(row, !row.is_active)}
                                                        >
                                                            {row.is_active ? 'Deactivate' : 'Reactivate'}
                                                        </button>
                                                        <button
                                                            type="button"
                                                            className="text-left text-rose-600 hover:underline"
                                                            disabled={busy}
                                                            onClick={() => removeUser(row, true)}
                                                        >
                                                            Delete
                                                        </button>
                                                    </div>
                                                </td>
                                            </tr>
                                        ))
                                    )}
                                </tbody>
                            </table>
                            <div className="flex items-center justify-between border-t border-slate-200 px-3 py-2 text-xs text-slate-500 dark:border-slate-800">
                                <span>
                                    Showing {userFrom}-{userTo} of {userTotal}
                                </span>
                                <div className="flex gap-2">
                                    <button
                                        type="button"
                                        className="rounded border border-slate-300 px-2 py-1 disabled:opacity-40 dark:border-slate-700"
                                        disabled={userOffset === 0 || busy}
                                        onClick={() => setUserOffset((v) => Math.max(0, v - pageSize))}
                                    >
                                        Prev
                                    </button>
                                    <button
                                        type="button"
                                        className="rounded border border-slate-300 px-2 py-1 disabled:opacity-40 dark:border-slate-700"
                                        disabled={userOffset + pageSize >= userTotal || busy}
                                        onClick={() => setUserOffset((v) => v + pageSize)}
                                    >
                                        Next
                                    </button>
                                </div>
                            </div>
                        </div>

                        <div className="rounded-xl border border-slate-200 bg-white p-4 lg:col-span-2 dark:border-slate-800 dark:bg-slate-900">
                            <h2 className="font-semibold">Login history</h2>
                            {!selected ? (
                                <p className="mt-2 text-sm text-slate-500">Select an account to see where they signed in.</p>
                            ) : (
                                <>
                                    <p className="mt-1 text-sm text-slate-500">{selected.email}</p>
                                    <ul className="mt-4 max-h-[28rem] space-y-3 overflow-y-auto text-sm">
                                        {events.length === 0 ? (
                                            <li className="text-slate-500">No login events yet.</li>
                                        ) : (
                                            events.map((ev) => (
                                                <li
                                                    key={ev.id}
                                                    className="rounded-lg border border-slate-100 px-3 py-2 dark:border-slate-800"
                                                >
                                                    <div className="font-medium">{formatWhen(ev.created_at)}</div>
                                                    <div className="text-slate-600 dark:text-slate-400">
                                                        IP: {ev.ip_address || '—'}
                                                    </div>
                                                    <div className="text-xs text-slate-500">
                                                        {ev.client?.label || 'Unknown device'}
                                                    </div>
                                                    <div className="truncate text-xs text-slate-500" title={ev.user_agent}>
                                                        {ev.user_agent || '—'}
                                                    </div>
                                                    {!ev.success ? (
                                                        <div className="text-xs text-rose-500">failed</div>
                                                    ) : null}
                                                </li>
                                            ))
                                        )}
                                    </ul>
                                </>
                            )}
                        </div>
                    </div>
                ) : (
                    <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
                        <table className="w-full text-left text-sm">
                            <thead className="border-b border-slate-200 text-slate-500 dark:border-slate-800">
                                <tr>
                                    <th className="px-3 py-2 font-medium">Organization</th>
                                    <th className="px-3 py-2 font-medium">Owner</th>
                                    <th className="px-3 py-2 font-medium">Seats</th>
                                    <th className="px-3 py-2 font-medium">Members</th>
                                    <th className="px-3 py-2 font-medium">Owner last login</th>
                                </tr>
                            </thead>
                            <tbody>
                                {organizations.length === 0 ? (
                                    <tr>
                                        <td colSpan={5} className="px-3 py-6 text-slate-500">
                                            No organizations found.
                                        </td>
                                    </tr>
                                ) : (
                                    organizations.map((org) => (
                                        <tr key={org.id} className="border-b border-slate-100 dark:border-slate-800">
                                            <td className="px-3 py-2">
                                                <div className="font-medium">{org.name}</div>
                                                <div className="text-xs text-slate-500">{formatWhen(org.created_at)}</div>
                                            </td>
                                            <td className="px-3 py-2">
                                                <div>{org.owner?.email}</div>
                                                {!org.owner?.is_active ? (
                                                    <div className="text-xs text-rose-500">owner inactive</div>
                                                ) : null}
                                            </td>
                                            <td className="px-3 py-2">
                                                {org.occupied_seats}/{org.seat_limit}
                                            </td>
                                            <td className="px-3 py-2">
                                                active {org.active_users} · inactive {org.inactive_users}
                                            </td>
                                            <td className="px-3 py-2">
                                                <div>{formatWhen(org.owner_latest_login?.created_at)}</div>
                                                <div className="text-xs text-slate-500">
                                                    {org.owner_latest_login?.ip_address || '—'} · {org.owner_latest_login?.client?.label || 'Unknown device'}
                                                </div>
                                            </td>
                                        </tr>
                                    ))
                                )}
                            </tbody>
                        </table>
                        <div className="flex items-center justify-between border-t border-slate-200 px-3 py-2 text-xs text-slate-500 dark:border-slate-800">
                            <span>
                                Showing {orgFrom}-{orgTo} of {orgTotal}
                            </span>
                            <div className="flex gap-2">
                                <button
                                    type="button"
                                    className="rounded border border-slate-300 px-2 py-1 disabled:opacity-40 dark:border-slate-700"
                                    disabled={orgOffset === 0 || busy}
                                    onClick={() => setOrgOffset((v) => Math.max(0, v - pageSize))}
                                >
                                    Prev
                                </button>
                                <button
                                    type="button"
                                    className="rounded border border-slate-300 px-2 py-1 disabled:opacity-40 dark:border-slate-700"
                                    disabled={orgOffset + pageSize >= orgTotal || busy}
                                    onClick={() => setOrgOffset((v) => v + pageSize)}
                                >
                                    Next
                                </button>
                            </div>
                        </div>
                    </div>
                )}

                <div className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
                    <h2 className="font-semibold">Recent admin actions</h2>
                    <ul className="mt-3 space-y-2 text-sm">
                        {auditEvents.length === 0 ? (
                            <li className="text-slate-500">No admin actions recorded yet.</li>
                        ) : (
                            auditEvents.map((ev) => (
                                <li key={ev.id} className="rounded border border-slate-100 px-3 py-2 dark:border-slate-800">
                                    <div className="font-medium">{ev.action}</div>
                                    <div className="text-xs text-slate-500">
                                        {formatWhen(ev.timestamp)} · actor {ev.actor_email || 'unknown'} · IP {ev.ip_address || '—'}
                                    </div>
                                    {ev.metadata?.target_email ? (
                                        <div className="text-xs text-slate-500">target: {ev.metadata.target_email}</div>
                                    ) : null}
                                </li>
                            ))
                        )}
                    </ul>
                </div>
            </main>
        </div>
    );
}
