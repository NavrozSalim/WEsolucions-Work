import { useCallback, useContext, useEffect, useState } from 'react';
import { Link, Navigate, useSearchParams } from 'react-router-dom';
import {
    Building2,
    RefreshCw,
    ShieldAlert,
    UserCheck,
    Users,
    XCircle,
} from 'lucide-react';
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
import { Badge, EmptyState, KPICard, PageHeader } from '../../components/design';
import Button from '../../components/ui/Button';
import Input from '../../components/ui/Input';
import Select from '../../components/ui/Select';
import ConfirmModal from '../../components/ui/ConfirmModal';
import Toast from '../../components/ui/Toast';
import { Table, TableHead, TableBody, Th, Td, TableEmpty } from '../../components/ui/Table';

const ACCOUNT_TYPE_OPTIONS = [
    { value: '', label: 'All types' },
    { value: 'super_user', label: 'Super User' },
    { value: 'user_account', label: 'User Account' },
    { value: 'standalone', label: 'Standalone' },
];

const ACCOUNT_TYPE_LABELS = {
    super_user: 'Super User',
    user_account: 'User Account',
    standalone: 'Standalone',
    platform_admin: 'Platform Admin',
};

function formatWhen(iso) {
    if (!iso) return '—';
    try {
        return new Date(iso).toLocaleString();
    } catch {
        return iso;
    }
}

function accountTypeLabel(type) {
    return ACCOUNT_TYPE_LABELS[type] || type || '—';
}

function accountTypeBadgeVariant(type) {
    if (type === 'super_user') return 'accent';
    if (type === 'user_account') return 'default';
    if (type === 'standalone') return 'warning';
    return 'default';
}

export default function PlatformAdmin() {
    const { user, loading: authLoading } = useContext(AuthContext);
    const [searchParams] = useSearchParams();
    const view = searchParams.get('view') || 'overview';

    const [stats, setStats] = useState(null);
    const [users, setUsers] = useState([]);
    const [organizations, setOrganizations] = useState([]);
    const [auditEvents, setAuditEvents] = useState([]);
    const [filter, setFilter] = useState('');
    const [accountType, setAccountType] = useState('');
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const [initialLoading, setInitialLoading] = useState(true);
    const [userTotal, setUserTotal] = useState(0);
    const [userOffset, setUserOffset] = useState(0);
    const [orgTotal, setOrgTotal] = useState(0);
    const [orgOffset, setOrgOffset] = useState(0);
    const pageSize = 50;
    const [selected, setSelected] = useState(null);
    const [events, setEvents] = useState([]);
    const [detailLoading, setDetailLoading] = useState(false);
    const [confirm, setConfirm] = useState(null);
    const [toast, setToast] = useState({ open: false, message: '', variant: 'info' });

    const showToast = (message, variant = 'success') => {
        setToast({ open: true, message, variant });
    };

    const loadUsers = useCallback(async () => {
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
                getPlatformAudit({ limit: 40, offset: 0 }),
            ]);
            setStats(s);
            setAuditEvents(audits.events || []);
            if (view === 'users' || view === 'overview') {
                await loadUsers();
            }
            if (view === 'organizations' || view === 'overview') {
                await loadOrganizations();
            }
        } catch (err) {
            setError(err.response?.data?.detail || 'Could not load platform data.');
        } finally {
            setBusy(false);
            setInitialLoading(false);
        }
    }, [loadOrganizations, loadUsers, view]);

    useEffect(() => {
        if (user?.is_platform_admin) load();
    }, [user, load]);

    const openDetail = async (row) => {
        setDetailLoading(true);
        setError('');
        try {
            const data = await getPlatformUserDetail(row.id);
            setSelected(data.user);
            setEvents(data.login_events || []);
        } catch (err) {
            setError(err.response?.data?.detail || 'Could not load login history.');
        } finally {
            setDetailLoading(false);
        }
    };

    const runConfirmAction = async () => {
        if (!confirm?.row) return;
        const { type, row } = confirm;
        setBusy(true);
        setError('');
        try {
            if (type === 'activate' || type === 'deactivate') {
                await updatePlatformUser(row.id, { is_active: type === 'activate' });
                showToast(
                    type === 'activate'
                        ? `${row.email} reactivated.`
                        : `${row.email} deactivated.`
                );
                if (selected?.id === row.id) await openDetail(row);
            } else if (type === 'delete') {
                await removePlatformUser(row.id, { hard: true });
                showToast(`${row.email} permanently deleted.`);
                if (selected?.id === row.id) {
                    setSelected(null);
                    setEvents([]);
                }
            }
            setConfirm(null);
            await load();
        } catch (err) {
            setError(err.response?.data?.detail || 'Action failed.');
            setConfirm(null);
        } finally {
            setBusy(false);
        }
    };

    const userFrom = users.length ? userOffset + 1 : 0;
    const userTo = userOffset + users.length;
    const orgFrom = organizations.length ? orgOffset + 1 : 0;
    const orgTo = orgOffset + organizations.length;

    if (authLoading) {
        return (
            <div className="flex items-center justify-center py-24 text-sm text-slate-500">
                Loading…
            </div>
        );
    }
    if (!user?.is_platform_admin) {
        return <Navigate to="/login/master" replace />;
    }
    if (initialLoading) {
        return (
            <div className="mx-auto max-w-7xl space-y-6 animate-pulse">
                <div className="h-8 w-48 rounded bg-slate-200 dark:bg-slate-800" />
                <div className="h-4 w-72 rounded bg-slate-200 dark:bg-slate-800" />
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
                    {Array.from({ length: 5 }).map((_, i) => (
                        <div
                            key={i}
                            className="h-24 rounded-lg border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900"
                        />
                    ))}
                </div>
                <div className="h-64 rounded-lg border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900" />
            </div>
        );
    }

    const titles = {
        overview: {
            title: 'Platform overview',
            description: 'Monitor accounts, organizations, and recent admin activity.',
        },
        users: {
            title: 'Users',
            description: 'Search accounts, review sessions, and manage access.',
        },
        organizations: {
            title: 'Organizations',
            description: 'Seat usage, owners, and organization health.',
        },
        audit: {
            title: 'Audit log',
            description: 'Recent privileged actions taken by platform admins.',
        },
    };
    const header = titles[view] || titles.overview;

    return (
        <div className="mx-auto max-w-7xl space-y-6">
            <PageHeader
                title={header.title}
                description={header.description}
                actions={
                    <Button
                        type="button"
                        variant="secondary"
                        onClick={load}
                        disabled={busy}
                        className="gap-2"
                    >
                        <RefreshCw className={`h-4 w-4 ${busy ? 'animate-spin' : ''}`} />
                        Refresh
                    </Button>
                }
            />

            {error ? (
                <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-800 dark:bg-rose-900/20 dark:text-rose-300">
                    {error}
                </div>
            ) : null}

            {(view === 'overview' || view === 'users' || view === 'organizations') && stats ? (
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
                    <KPICard
                        label="Super Users"
                        value={stats.super_users}
                        icon={ShieldAlert}
                        tone="accent"
                        to="/platform?view=users"
                    />
                    <KPICard
                        label="User Accounts"
                        value={stats.user_accounts}
                        icon={Users}
                        to="/platform?view=users"
                    />
                    <KPICard
                        label="Organizations"
                        value={stats.organizations}
                        icon={Building2}
                        to="/platform?view=organizations"
                    />
                    <KPICard
                        label="Logins (24h)"
                        value={stats.recent_logins_24h}
                        icon={UserCheck}
                        tone="success"
                    />
                    <KPICard
                        label="Failed (24h)"
                        value={stats.failed_logins_24h}
                        icon={XCircle}
                        tone={stats.failed_logins_24h > 0 ? 'error' : 'default'}
                    />
                </div>
            ) : null}

            {view === 'overview' ? (
                <OverviewSection
                    auditEvents={auditEvents}
                    userTotal={userTotal}
                    orgTotal={orgTotal}
                />
            ) : null}

            {view === 'users' ? (
                <UsersSection
                    filter={filter}
                    setFilter={setFilter}
                    accountType={accountType}
                    setAccountType={setAccountType}
                    setUserOffset={setUserOffset}
                    setOrgOffset={setOrgOffset}
                    users={users}
                    selected={selected}
                    events={events}
                    detailLoading={detailLoading}
                    openDetail={openDetail}
                    setSelected={setSelected}
                    setEvents={setEvents}
                    setConfirm={setConfirm}
                    busy={busy}
                    userFrom={userFrom}
                    userTo={userTo}
                    userTotal={userTotal}
                    userOffset={userOffset}
                    pageSize={pageSize}
                />
            ) : null}

            {view === 'organizations' ? (
                <OrganizationsSection
                    filter={filter}
                    setFilter={setFilter}
                    setUserOffset={setUserOffset}
                    setOrgOffset={setOrgOffset}
                    organizations={organizations}
                    orgFrom={orgFrom}
                    orgTo={orgTo}
                    orgTotal={orgTotal}
                    orgOffset={orgOffset}
                    pageSize={pageSize}
                    busy={busy}
                />
            ) : null}

            {view === 'audit' ? <AuditSection auditEvents={auditEvents} /> : null}

            <ConfirmModal
                open={Boolean(confirm)}
                onClose={() => setConfirm(null)}
                onConfirm={runConfirmAction}
                loading={busy}
                variant={confirm?.type === 'delete' ? 'danger' : 'primary'}
                title={
                    confirm?.type === 'delete'
                        ? 'Delete account'
                        : confirm?.type === 'deactivate'
                          ? 'Deactivate account'
                          : 'Reactivate account'
                }
                message={
                    confirm?.type === 'delete'
                        ? `Permanently delete ${confirm?.row?.email}? This cannot be undone.`
                        : confirm?.type === 'deactivate'
                          ? `Deactivate ${confirm?.row?.email}? They will not be able to sign in.`
                          : `Reactivate ${confirm?.row?.email}?`
                }
                confirmLabel={
                    confirm?.type === 'delete'
                        ? 'Delete permanently'
                        : confirm?.type === 'deactivate'
                          ? 'Deactivate'
                          : 'Reactivate'
                }
            />

            <Toast
                open={toast.open}
                message={toast.message}
                variant={toast.variant}
                onClose={() => setToast((t) => ({ ...t, open: false }))}
            />
        </div>
    );
}

function OverviewSection({ auditEvents, userTotal, orgTotal }) {
    return (
        <div className="grid gap-6 lg:grid-cols-2">
            <section className="rounded-lg border border-slate-200 bg-white p-5 dark:border-slate-700 dark:bg-slate-900">
                <div className="flex items-center justify-between gap-3">
                    <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                        Quick links
                    </h2>
                </div>
                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                    <Link
                        to="/platform?view=users"
                        className="rounded-lg border border-slate-200 p-4 transition-colors hover:border-accent-400 dark:border-slate-700 dark:hover:border-accent-500"
                    >
                        <Users className="h-5 w-5 text-accent-500" />
                        <p className="mt-2 text-sm font-medium text-slate-900 dark:text-slate-100">
                            Manage users
                        </p>
                        <p className="mt-0.5 text-xs text-slate-500">{userTotal} accounts</p>
                    </Link>
                    <Link
                        to="/platform?view=organizations"
                        className="rounded-lg border border-slate-200 p-4 transition-colors hover:border-accent-400 dark:border-slate-700 dark:hover:border-accent-500"
                    >
                        <Building2 className="h-5 w-5 text-accent-500" />
                        <p className="mt-2 text-sm font-medium text-slate-900 dark:text-slate-100">
                            Organizations
                        </p>
                        <p className="mt-0.5 text-xs text-slate-500">{orgTotal} orgs</p>
                    </Link>
                </div>
            </section>

            <section className="rounded-lg border border-slate-200 bg-white p-5 dark:border-slate-700 dark:bg-slate-900">
                <div className="flex items-center justify-between gap-3">
                    <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                        Recent admin actions
                    </h2>
                    <Link
                        to="/platform?view=audit"
                        className="text-xs font-medium text-accent-600 hover:underline dark:text-accent-400"
                    >
                        View all
                    </Link>
                </div>
                <AuditList events={auditEvents.slice(0, 6)} compact />
            </section>
        </div>
    );
}

function UsersSection({
    filter,
    setFilter,
    accountType,
    setAccountType,
    setUserOffset,
    setOrgOffset,
    users,
    selected,
    events,
    detailLoading,
    openDetail,
    setSelected,
    setEvents,
    setConfirm,
    busy,
    userFrom,
    userTo,
    userTotal,
    userOffset,
    pageSize,
}) {
    return (
        <div className="space-y-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
                <Input
                    label="Search"
                    value={filter}
                    onChange={(e) => {
                        setFilter(e.target.value);
                        setUserOffset(0);
                        setOrgOffset(0);
                    }}
                    placeholder="email, name, or organization"
                    className="min-w-0 flex-1"
                />
                <Select
                    label="Account type"
                    options={ACCOUNT_TYPE_OPTIONS}
                    value={accountType}
                    onChange={(e) => {
                        setAccountType(e.target.value);
                        setUserOffset(0);
                    }}
                    className="w-full sm:w-48"
                />
            </div>

            <div className="grid gap-6 lg:grid-cols-5">
                <div className="lg:col-span-3">
                    <Table>
                        <TableHead>
                            <Th>Account</Th>
                            <Th>Type</Th>
                            <Th>Last login</Th>
                            <Th className="text-right">Actions</Th>
                        </TableHead>
                        <TableBody>
                            {users.length === 0 ? (
                                <TableEmpty colSpan={4} message="No accounts found." />
                            ) : (
                                users.map((row) => {
                                    const isSelected = selected?.id === row.id;
                                    return (
                                        <tr
                                            key={row.id}
                                            className={
                                                isSelected
                                                    ? 'bg-accent-50/60 dark:bg-accent-900/10'
                                                    : undefined
                                            }
                                        >
                                            <Td className="whitespace-normal">
                                                <button
                                                    type="button"
                                                    className="text-left font-medium text-accent-700 hover:underline dark:text-accent-400"
                                                    onClick={() => openDetail(row)}
                                                >
                                                    {row.email}
                                                </button>
                                                <div className="mt-1 flex flex-wrap items-center gap-1.5">
                                                    {!row.is_active ? (
                                                        <Badge variant="error">Inactive</Badge>
                                                    ) : (
                                                        <Badge variant="success">Active</Badge>
                                                    )}
                                                    <span className="text-xs text-slate-500">
                                                        {row.organization?.name || 'No organization'}
                                                    </span>
                                                </div>
                                            </Td>
                                            <Td>
                                                <Badge variant={accountTypeBadgeVariant(row.account_type)}>
                                                    {accountTypeLabel(row.account_type)}
                                                </Badge>
                                            </Td>
                                            <Td className="whitespace-normal">
                                                <div>
                                                    {formatWhen(
                                                        row.latest_login?.created_at || row.last_login
                                                    )}
                                                </div>
                                                <div className="text-xs text-slate-500">
                                                    {row.latest_login?.ip_address || '—'} ·{' '}
                                                    {row.latest_login?.client?.label || 'Unknown device'}
                                                </div>
                                            </Td>
                                            <Td className="text-right">
                                                <div className="flex flex-wrap justify-end gap-1">
                                                    <Button
                                                        type="button"
                                                        size="sm"
                                                        variant="ghost"
                                                        onClick={() => openDetail(row)}
                                                    >
                                                        Sessions
                                                    </Button>
                                                    <Button
                                                        type="button"
                                                        size="sm"
                                                        variant="secondary"
                                                        disabled={busy}
                                                        onClick={() =>
                                                            setConfirm({
                                                                type: row.is_active
                                                                    ? 'deactivate'
                                                                    : 'activate',
                                                                row,
                                                            })
                                                        }
                                                    >
                                                        {row.is_active ? 'Deactivate' : 'Reactivate'}
                                                    </Button>
                                                    <Button
                                                        type="button"
                                                        size="sm"
                                                        variant="danger"
                                                        disabled={busy}
                                                        onClick={() =>
                                                            setConfirm({ type: 'delete', row })
                                                        }
                                                    >
                                                        Delete
                                                    </Button>
                                                </div>
                                            </Td>
                                        </tr>
                                    );
                                })
                            )}
                        </TableBody>
                    </Table>
                    <Pagination
                        from={userFrom}
                        to={userTo}
                        total={userTotal}
                        offset={userOffset}
                        pageSize={pageSize}
                        busy={busy}
                        onPrev={() => setUserOffset((v) => Math.max(0, v - pageSize))}
                        onNext={() => setUserOffset((v) => v + pageSize)}
                    />
                </div>

                <aside className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900 lg:col-span-2">
                    <div className="flex items-start justify-between gap-2">
                        <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                            Login history
                        </h2>
                        {selected ? (
                            <button
                                type="button"
                                className="text-xs text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"
                                onClick={() => {
                                    setSelected(null);
                                    setEvents([]);
                                }}
                            >
                                Clear
                            </button>
                        ) : null}
                    </div>

                    {detailLoading ? (
                        <p className="mt-6 text-sm text-slate-500">Loading sessions…</p>
                    ) : !selected ? (
                        <EmptyState
                            icon={Users}
                            title="Select an account"
                            description="Choose a user to inspect recent sign-ins, IPs, and devices."
                        />
                    ) : (
                        <>
                            <div className="mt-3 rounded-md border border-slate-100 bg-slate-50 px-3 py-2 dark:border-slate-800 dark:bg-slate-950/50">
                                <p className="truncate text-sm font-medium text-slate-900 dark:text-slate-100">
                                    {selected.email}
                                </p>
                                <div className="mt-1 flex flex-wrap gap-1.5">
                                    <Badge variant={accountTypeBadgeVariant(selected.account_type)}>
                                        {accountTypeLabel(selected.account_type)}
                                    </Badge>
                                    <Badge variant={selected.is_active ? 'success' : 'error'}>
                                        {selected.is_active ? 'Active' : 'Inactive'}
                                    </Badge>
                                </div>
                            </div>
                            <ul className="mt-4 max-h-[28rem] space-y-2 overflow-y-auto">
                                {events.length === 0 ? (
                                    <li className="py-8 text-center text-sm text-slate-500">
                                        No login events yet.
                                    </li>
                                ) : (
                                    events.map((ev) => (
                                        <li
                                            key={ev.id}
                                            className="rounded-md border border-slate-100 px-3 py-2.5 dark:border-slate-800"
                                        >
                                            <div className="flex items-start justify-between gap-2">
                                                <p className="text-sm font-medium text-slate-900 dark:text-slate-100">
                                                    {formatWhen(ev.created_at)}
                                                </p>
                                                <Badge variant={ev.success ? 'success' : 'error'}>
                                                    {ev.success ? 'Success' : 'Failed'}
                                                </Badge>
                                            </div>
                                            <p className="mt-1 text-xs text-slate-500">
                                                IP {ev.ip_address || '—'} ·{' '}
                                                {ev.client?.label || 'Unknown device'}
                                            </p>
                                            <p
                                                className="mt-0.5 truncate text-[11px] text-slate-400"
                                                title={ev.user_agent}
                                            >
                                                {ev.user_agent || '—'}
                                            </p>
                                        </li>
                                    ))
                                )}
                            </ul>
                        </>
                    )}
                </aside>
            </div>
        </div>
    );
}

function OrganizationsSection({
    filter,
    setFilter,
    setUserOffset,
    setOrgOffset,
    organizations,
    orgFrom,
    orgTo,
    orgTotal,
    orgOffset,
    pageSize,
    busy,
}) {
    return (
        <div className="space-y-4">
            <Input
                label="Search"
                value={filter}
                onChange={(e) => {
                    setFilter(e.target.value);
                    setUserOffset(0);
                    setOrgOffset(0);
                }}
                placeholder="organization or owner email"
                className="max-w-md"
            />

            <Table>
                <TableHead>
                    <Th>Organization</Th>
                    <Th>Owner</Th>
                    <Th>Seats</Th>
                    <Th>Members</Th>
                    <Th>Owner last login</Th>
                </TableHead>
                <TableBody>
                    {organizations.length === 0 ? (
                        <TableEmpty colSpan={5} message="No organizations found." />
                    ) : (
                        organizations.map((org) => {
                            const seatPct =
                                org.seat_limit > 0
                                    ? Math.round((org.occupied_seats / org.seat_limit) * 100)
                                    : 0;
                            return (
                                <tr key={org.id}>
                                    <Td className="whitespace-normal">
                                        <div className="font-medium">{org.name}</div>
                                        <div className="text-xs text-slate-500">
                                            Created {formatWhen(org.created_at)}
                                        </div>
                                    </Td>
                                    <Td className="whitespace-normal">
                                        <div>{org.owner?.email || '—'}</div>
                                        {!org.owner?.is_active ? (
                                            <Badge variant="error" className="mt-1">
                                                Owner inactive
                                            </Badge>
                                        ) : null}
                                    </Td>
                                    <Td>
                                        <div className="font-medium tabular-nums">
                                            {org.occupied_seats}/{org.seat_limit}
                                        </div>
                                        <div className="mt-1 h-1.5 w-20 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                                            <div
                                                className={`h-full rounded-full ${
                                                    seatPct >= 90
                                                        ? 'bg-rose-500'
                                                        : seatPct >= 70
                                                          ? 'bg-amber-500'
                                                          : 'bg-accent-500'
                                                }`}
                                                style={{ width: `${Math.min(seatPct, 100)}%` }}
                                            />
                                        </div>
                                    </Td>
                                    <Td className="whitespace-normal">
                                        <span className="text-emerald-600 dark:text-emerald-400">
                                            {org.active_users} active
                                        </span>
                                        <span className="text-slate-400"> · </span>
                                        <span className="text-slate-500">
                                            {org.inactive_users} inactive
                                        </span>
                                    </Td>
                                    <Td className="whitespace-normal">
                                        <div>{formatWhen(org.owner_latest_login?.created_at)}</div>
                                        <div className="text-xs text-slate-500">
                                            {org.owner_latest_login?.ip_address || '—'} ·{' '}
                                            {org.owner_latest_login?.client?.label || 'Unknown device'}
                                        </div>
                                    </Td>
                                </tr>
                            );
                        })
                    )}
                </TableBody>
            </Table>
            <Pagination
                from={orgFrom}
                to={orgTo}
                total={orgTotal}
                offset={orgOffset}
                pageSize={pageSize}
                busy={busy}
                onPrev={() => setOrgOffset((v) => Math.max(0, v - pageSize))}
                onNext={() => setOrgOffset((v) => v + pageSize)}
            />
        </div>
    );
}

function AuditSection({ auditEvents }) {
    if (!auditEvents.length) {
        return (
            <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900">
                <EmptyState
                    icon={ShieldAlert}
                    title="No admin actions yet"
                    description="Privileged platform actions will appear here once recorded."
                />
            </div>
        );
    }

    return (
        <section className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900 sm:p-5">
            <AuditList events={auditEvents} />
        </section>
    );
}

function AuditList({ events, compact = false }) {
    if (!events.length) {
        return <p className="mt-4 text-sm text-slate-500">No admin actions recorded yet.</p>;
    }

    return (
        <ul className={`mt-3 space-y-2 ${compact ? '' : ''}`}>
            {events.map((ev) => (
                <li
                    key={ev.id}
                    className="rounded-md border border-slate-100 px-3 py-2.5 dark:border-slate-800"
                >
                    <div className="flex flex-wrap items-center gap-2">
                        <Badge variant="accent">{ev.action}</Badge>
                        <span className="text-xs text-slate-500">{formatWhen(ev.timestamp)}</span>
                    </div>
                    <p className="mt-1.5 text-xs text-slate-500">
                        Actor {ev.actor_email || 'unknown'} · IP {ev.ip_address || '—'}
                    </p>
                    {ev.metadata?.target_email ? (
                        <p className="mt-0.5 text-xs text-slate-500">
                            Target: {ev.metadata.target_email}
                        </p>
                    ) : null}
                </li>
            ))}
        </ul>
    );
}

function Pagination({ from, to, total, offset, pageSize, busy, onPrev, onNext }) {
    return (
        <div className="mt-3 flex items-center justify-between text-xs text-slate-500">
            <span>
                Showing {from}-{to} of {total}
            </span>
            <div className="flex gap-2">
                <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    disabled={offset === 0 || busy}
                    onClick={onPrev}
                >
                    Previous
                </Button>
                <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    disabled={offset + pageSize >= total || busy}
                    onClick={onNext}
                >
                    Next
                </Button>
            </div>
        </div>
    );
}
