import { createContext, useCallback, useContext, useMemo, useState } from 'react';

const SidebarActivityContext = createContext(null);

/**
 * App-wide sidebar activities (loading / long-running jobs).
 * Pages set entries by id; layout renders them and clears when callers remove them.
 */
export function SidebarActivityProvider({ children }) {
    const [activities, setActivities] = useState({});

    const setSidebarActivity = useCallback((id, payload) => {
        setActivities((prev) => ({
            ...prev,
            [id]: {
                id,
                title: payload.title || '',
                description: payload.description || '',
                progress: payload.progress != null ? payload.progress : null,
            },
        }));
    }, []);

    const patchSidebarActivity = useCallback((id, partial) => {
        setActivities((prev) => {
            const cur = prev[id];
            if (!cur) return prev;
            return {
                ...prev,
                [id]: {
                    ...cur,
                    ...partial,
                    progress: partial.progress != null ? partial.progress : cur.progress,
                },
            };
        });
    }, []);

    const clearSidebarActivity = useCallback((id) => {
        setActivities((prev) => {
            if (!(id in prev)) return prev;
            const next = { ...prev };
            delete next[id];
            return next;
        });
    }, []);

    const clearCatalogActivities = useCallback(() => {
        setActivities((prev) => {
            const next = { ...prev };
            let changed = false;
            Object.keys(next).forEach((k) => {
                if (k.startsWith('catalog-')) {
                    delete next[k];
                    changed = true;
                }
            });
            return changed ? next : prev;
        });
    }, []);

    const value = useMemo(
        () => ({
            activities,
            setSidebarActivity,
            patchSidebarActivity,
            clearSidebarActivity,
            clearCatalogActivities,
        }),
        [activities, setSidebarActivity, patchSidebarActivity, clearSidebarActivity, clearCatalogActivities],
    );

    return <SidebarActivityContext.Provider value={value}>{children}</SidebarActivityContext.Provider>;
}

export function useSidebarActivity() {
    const ctx = useContext(SidebarActivityContext);
    if (!ctx) {
        throw new Error('useSidebarActivity must be used within SidebarActivityProvider');
    }
    return ctx;
}
