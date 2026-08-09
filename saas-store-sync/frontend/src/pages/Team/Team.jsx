import { useCallback, useContext, useEffect, useState } from 'react';
import { AuthContext } from '../../context/AuthContext';
import { useI18n } from '../../context/I18nContext';
import PageHeader from '../../components/design/PageHeader';
import Button from '../../components/ui/Button';
import Input from '../../components/ui/Input';
import Modal from '../../components/ui/Modal';
import {
    createTeamMember,
    deleteTeamMember,
    getTeam,
    updateTeamMember,
} from '../../services/authService';

const EMPTY_PERMS = {
    dashboard: true,
    stores: false,
    catalog: true,
    orders: true,
    tickets: true,
    team: false,
};

export default function Team() {
    const { t } = useI18n();
    const { refreshProfile } = useContext(AuthContext);
    const [org, setOrg] = useState(null);
    const [members, setMembers] = useState([]);
    const [permissionKeys, setPermissionKeys] = useState([]);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);
    const [modalOpen, setModalOpen] = useState(false);
    const [editMember, setEditMember] = useState(null);
    const [form, setForm] = useState({
        email: '',
        password: '',
        first_name: '',
        last_name: '',
        permissions: { ...EMPTY_PERMS },
    });
    const [busy, setBusy] = useState(false);

    const load = useCallback(async () => {
        setError('');
        try {
            const data = await getTeam();
            setOrg(data.organization);
            setMembers(data.members || []);
            setPermissionKeys(data.permission_keys || []);
        } catch (err) {
            setError(err.response?.data?.detail || 'Could not load team.');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        load();
    }, [load]);

    const openCreate = () => {
        setEditMember(null);
        setForm({
            email: '',
            password: '',
            first_name: '',
            last_name: '',
            permissions: { ...EMPTY_PERMS },
        });
        setModalOpen(true);
    };

    const openEdit = (member) => {
        setEditMember(member);
        setForm({
            email: member.email,
            password: '',
            first_name: member.first_name || '',
            last_name: member.last_name || '',
            permissions: { ...EMPTY_PERMS, ...(member.permissions || {}) },
        });
        setModalOpen(true);
    };

    const togglePerm = (key) => {
        if (key === 'team') return;
        setForm((f) => ({
            ...f,
            permissions: { ...f.permissions, [key]: !f.permissions[key] },
        }));
    };

    const submitMember = async (e) => {
        e.preventDefault();
        setBusy(true);
        setError('');
        try {
            if (editMember) {
                const payload = {
                    first_name: form.first_name,
                    last_name: form.last_name,
                    permissions: { ...form.permissions, team: false },
                };
                if (form.password) payload.password = form.password;
                await updateTeamMember(editMember.id, payload);
            } else {
                await createTeamMember({
                    email: form.email.trim().toLowerCase(),
                    password: form.password,
                    first_name: form.first_name,
                    last_name: form.last_name,
                    permissions: { ...form.permissions, team: false },
                });
            }
            setModalOpen(false);
            await load();
            await refreshProfile?.();
        } catch (err) {
            setError(err.response?.data?.detail || 'Save failed.');
        } finally {
            setBusy(false);
        }
    };

    const removeMember = async (member) => {
        if (!window.confirm(`Remove ${member.email}?`)) return;
        try {
            await deleteTeamMember(member.id);
            await load();
            await refreshProfile?.();
        } catch (err) {
            setError(err.response?.data?.detail || 'Delete failed.');
        }
    };

    if (loading) {
        return <div className="p-6 text-slate-500">Loading team…</div>;
    }

    return (
        <div className="space-y-6">
            <PageHeader
                title={t('team.title')}
                description={t('team.subtitle')}
                actions={
                    <Button type="button" onClick={openCreate}>
                        {t('team.addMember')}
                    </Button>
                }
            />

            {error && (
                <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:border-rose-800 dark:bg-rose-900/20 dark:text-rose-300">
                    {error}
                </div>
            )}

            {org && (
                <div className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
                    <p className="text-sm font-medium text-slate-900 dark:text-slate-100">
                        {org.name}
                    </p>
                    <p className="text-sm text-slate-500">
                        {t('team.seats', {
                            occupied: org.occupied_seats,
                            limit: org.seat_limit,
                        })}
                    </p>
                </div>
            )}

            <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900">
                <table className="table-base">
                    <thead>
                        <tr>
                            <th>{t('team.email')}</th>
                            <th>{t('team.name')}</th>
                            <th>Role</th>
                            <th>{t('team.permissions')}</th>
                            <th>{t('team.actions')}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {members.length === 0 && (
                            <tr>
                                <td colSpan={5} className="text-slate-500">
                                    {t('team.noMembers')}
                                </td>
                            </tr>
                        )}
                        {members.map((m) => (
                            <tr key={m.id}>
                                <td className="font-medium">{m.email}</td>
                                <td>
                                    {[m.first_name, m.last_name].filter(Boolean).join(' ') || '—'}
                                </td>
                                <td>
                                    {m.account_type === 'super_user'
                                        ? t('team.owner')
                                        : t('team.member')}
                                    {!m.is_active && (
                                        <span className="ml-2 text-xs text-rose-500">
                                            {t('team.inactive')}
                                        </span>
                                    )}
                                </td>
                                <td className="max-w-xs text-xs text-slate-500">
                                    {Object.entries(m.permissions || {})
                                        .filter(([, v]) => v)
                                        .map(([k]) => k)
                                        .join(', ') || '—'}
                                </td>
                                <td>
                                    {m.account_type !== 'super_user' && (
                                        <div className="flex gap-2">
                                            <button
                                                type="button"
                                                className="text-sm text-sky-700 hover:underline"
                                                onClick={() => openEdit(m)}
                                            >
                                                Edit
                                            </button>
                                            <button
                                                type="button"
                                                className="text-sm text-rose-600 hover:underline"
                                                onClick={() => removeMember(m)}
                                            >
                                                {t('team.delete')}
                                            </button>
                                        </div>
                                    )}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            <Modal
                open={modalOpen}
                onClose={() => setModalOpen(false)}
                title={editMember ? 'Edit user account' : t('team.addMember')}
            >
                <form className="space-y-4" onSubmit={submitMember}>
                    {!editMember && (
                        <Input
                            type="email"
                            label={t('team.email')}
                            required
                            value={form.email}
                            onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
                            placeholder="user@company.com"
                        />
                    )}
                    <div className="grid grid-cols-2 gap-3">
                        <Input
                            label="First name"
                            value={form.first_name}
                            onChange={(e) => setForm((f) => ({ ...f, first_name: e.target.value }))}
                        />
                        <Input
                            label="Last name"
                            value={form.last_name}
                            onChange={(e) => setForm((f) => ({ ...f, last_name: e.target.value }))}
                        />
                    </div>
                    <Input
                        type="password"
                        label={editMember ? 'New password (optional)' : 'Password'}
                        required={!editMember}
                        minLength={8}
                        value={form.password}
                        onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))}
                    />
                    <fieldset>
                        <legend className="text-sm font-medium text-slate-700 dark:text-slate-300">
                            {t('team.permissions')}
                        </legend>
                        <div className="mt-2 grid grid-cols-2 gap-2">
                            {(permissionKeys.length
                                ? permissionKeys
                                : Object.keys(EMPTY_PERMS).map((key) => ({ key, label: key }))
                            )
                                .filter((p) => p.key !== 'team')
                                .map((p) => (
                                    <label
                                        key={p.key}
                                        className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300"
                                    >
                                        <input
                                            type="checkbox"
                                            checked={Boolean(form.permissions[p.key])}
                                            onChange={() => togglePerm(p.key)}
                                        />
                                        {p.label || p.key}
                                    </label>
                                ))}
                        </div>
                    </fieldset>
                    <div className="flex justify-end gap-2 pt-2">
                        <Button type="button" variant="secondary" onClick={() => setModalOpen(false)}>
                            Cancel
                        </Button>
                        <Button type="submit" disabled={busy}>
                            {editMember ? t('team.save') : t('team.create')}
                        </Button>
                    </div>
                </form>
            </Modal>
        </div>
    );
}
