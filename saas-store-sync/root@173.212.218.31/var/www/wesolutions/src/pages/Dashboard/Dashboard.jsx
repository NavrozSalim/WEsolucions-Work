import { useEffect, useState } from 'react';
import { getDashboardSummary, getAnalyticsCharts, getStores } from '../../services/storeService';
import {
    LineChart,
    Line,
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip,
    ResponsiveContainer,
} from 'recharts';
import { Package, AlertTriangle, ShieldCheck, Store } from 'lucide-react';
import { KPICard, PageHeader } from '../../components/design';

export default function Dashboard() {
    const [summary, setSummary] = useState({
        total_products: 0,
        catalog_count: 0,
        out_of_stock_count: 0,
        store_breakdown: [],
    });
    const [chartData, setChartData] = useState({ out_of_stock: [] });
    const [range, setRange] = useState('30');
    const [loading, setLoading] = useState(true);
    const [stores, setStores] = useState([]);
    const [selectedStore, setSelectedStore] = useState('');

    useEffect(() => {
        getStores().then((res) => setStores(Array.isArray(res.data) ? res.data : res.data?.results || [])).catch(() => {});
    }, []);

    useEffect(() => {
        setLoading(true);
        const params = selectedStore ? { store_id: selectedStore } : undefined;
        getDashboardSummary(params)
            .then((res) => setSummary(res.data))
            .catch(() => {})
            .finally(() => setLoading(false));
    }, [selectedStore]);

    useEffect(() => {
        const params = { range };
        if (selectedStore) params.store_id = selectedStore;
        getAnalyticsCharts(params)
            .then((res) => {
                const d = res.data;
                setChartData({ out_of_stock: d?.out_of_stock || [] });
            })
            .catch(() => {});
    }, [range, selectedStore]);

    const catalogCount = summary.catalog_count ?? summary.total_products;
    const outOfStock = summary.out_of_stock_count ?? 0;
    const storesCount = summary.store_breakdown?.length ?? 0;
    const needsAttention = summary.store_breakdown?.reduce(
        (a, s) => a + (s.needs_attention_count || 0),
        0
    ) ?? 0;

    const kpis = [
        { label: 'Active Listings', value: loading ? '–' : catalogCount, sub: 'Catalog products', icon: Package },
        { label: 'Sync Accuracy', value: '98.4%', sub: 'Last 24 hours', icon: ShieldCheck },
        { label: 'Low Stock Alerts', value: loading ? '–' : outOfStock, sub: 'Needs attention', icon: AlertTriangle },
        { label: 'Stores', value: storesCount, sub: 'Connected', icon: Store },
    ];

    return (
        <div className="space-y-6">
            <PageHeader
                title="Dashboard"
                description="Overview of your store sync operations and listing health."
                actions={
                    <div className="flex items-center gap-3">
                        <select
                            value={selectedStore}
                            onChange={(e) => setSelectedStore(e.target.value)}
                            className="rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-1.5 text-sm font-medium text-slate-700 dark:text-slate-300 focus:border-accent-500 focus:ring-1 focus:ring-accent-500 outline-none min-w-[160px]"
                        >
                            <option value="">All stores</option>
                            {stores.map((s) => (
                                <option key={s.id} value={s.id}>{s.name}</option>
                            ))}
                        </select>
                        <div className="flex gap-1">
                            {['7', '30'].map((r) => (
                                <button
                                    key={r}
                                    type="button"
                                    onClick={() => setRange(r)}
                                    className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                                        range === r
                                            ? 'bg-accent-600 text-white dark:bg-accent-500'
                                            : 'text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800'
                                    }`}
                                >
                                    {r} days
                                </button>
                            ))}
                        </div>
                    </div>
                }
            />

            {/* KPI row */}
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {kpis.map((item) => (
                    <KPICard key={item.label} {...item} />
                ))}
            </div>

            <div className="grid gap-6 lg:grid-cols-3">
                {/* Chart */}
                <div className="lg:col-span-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-5">
                    <h2 className="text-base font-medium text-slate-900 dark:text-slate-100">
                        Out-of-stock trend
                    </h2>
                    <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
                        Inventory health over the last {range} days.
                    </p>
                    <div className="mt-4 h-64">
                        {chartData.out_of_stock?.length > 0 ? (
                            <ResponsiveContainer width="100%" height="100%">
                                <LineChart data={chartData.out_of_stock}>
                                    <CartesianGrid
                                        strokeDasharray="3 3"
                                        stroke="currentColor"
                                        className="stroke-slate-200 dark:stroke-slate-700"
                                    />
                                    <XAxis
                                        dataKey="date"
                                        tick={{ fontSize: 11 }}
                                        stroke="currentColor"
                                        className="text-slate-500"
                                    />
                                    <YAxis
                                        tick={{ fontSize: 11 }}
                                        stroke="currentColor"
                                        className="text-slate-500"
                                    />
                                    <Tooltip
                                        contentStyle={{
                                            backgroundColor: 'var(--tw-bg-opacity, 1)',
                                            border: '1px solid rgb(226 232 240)',
                                            borderRadius: '8px',
                                            fontSize: '12px',
                                        }}
                                        labelStyle={{ color: 'inherit' }}
                                    />
                                    <Line
                                        type="monotone"
                                        dataKey="count"
                                        stroke="#2563eb"
                                        strokeWidth={2}
                                        name="Out of stock"
                                        dot={{ r: 3, fill: '#2563eb' }}
                                    />
                                </LineChart>
                            </ResponsiveContainer>
                        ) : (
                            <div className="flex h-full items-center justify-center text-sm text-slate-500 dark:text-slate-400">
                                No data yet. Add stores and run sync.
                            </div>
                        )}
                    </div>
                </div>

                {/* Side panel */}
                <div className="space-y-6">
                    {summary.store_breakdown?.length > 0 && (
                        <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-5">
                            <h3 className="text-base font-medium text-slate-900 dark:text-slate-100">
                                Products by store
                            </h3>
                            <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
                                Breakdown across your stores.
                            </p>
                            <div className="mt-4 space-y-3">
                                {summary.store_breakdown.map((s) => (
                                    <div
                                        key={s.store_id}
                                        className="rounded-md border border-slate-100 dark:border-slate-800 bg-slate-50 dark:bg-slate-800/50 p-3"
                                    >
                                        <div className="flex items-center justify-between">
                                            <p className="text-sm font-medium text-slate-900 dark:text-slate-100">
                                                {s.store_name}
                                            </p>
                                            <span className="text-sm text-slate-500 dark:text-slate-400">
                                                {s.product_count} total
                                            </span>
                                        </div>
                                        <div className="mt-2 flex gap-4 text-xs text-slate-500 dark:text-slate-400">
                                            <span className="text-emerald-600 dark:text-emerald-500">
                                                {s.synced_count} synced
                                            </span>
                                            <span className="text-amber-600 dark:text-amber-500">
                                                {s.needs_attention_count} needs attention
                                            </span>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-5">
                        <div className="flex items-center justify-between">
                            <h3 className="text-base font-medium text-slate-900 dark:text-slate-100">
                                Sync health
                            </h3>
                            <span className="rounded-md bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400">
                                Stable
                            </span>
                        </div>
                        <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
                            Quick read on system health.
                        </p>
                        <div className="mt-4 space-y-4">
                            {(() => {
                                const items = [
                                    { label: 'Synced listings', value: catalogCount },
                                    { label: 'Needs attention', value: needsAttention },
                                    { label: 'Out of stock', value: outOfStock },
                                ];
                                const maxVal = Math.max(catalogCount, needsAttention, outOfStock, 1);
                                return items.map((item) => ({
                                    ...item,
                                    pct: Math.round((item.value / maxVal) * 100),
                                }));
                            })().map((item) => (
                                <div key={item.label}>
                                    <div className="mb-1.5 flex items-center justify-between text-sm">
                                        <span className="text-slate-500 dark:text-slate-400">
                                            {item.label}
                                        </span>
                                        <span className="font-medium text-slate-900 dark:text-slate-100">
                                            {item.value}
                                        </span>
                                    </div>
                                    <div className="h-1.5 rounded-full bg-slate-100 dark:bg-slate-800">
                                        <div
                                            className="h-1.5 rounded-full bg-accent-500"
                                            style={{ width: `${item.pct}%` }}
                                        />
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
