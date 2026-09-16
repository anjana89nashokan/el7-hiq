import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronDown, Plus, Search } from "lucide-react";
import { useNavigate } from "react-router-dom";
import MappingsLibraryDetailModal from "../components/mappings-library/MappingsLibraryDetailModal";
import {
  deleteHL7Session,
  downloadHL7Mappings,
  listHL7Sessions,
} from "../end-points/hl7Api";
import { sttmNav } from "../utils/sttmRoutes";
import {
  DEMO_MAPPINGS,
  GROUP_ORDER,
  STATUS_LABEL,
  fmtDate,
  groupKey,
  groupLabel,
  librarySnapshotStats,
  mergeLibraryItems,
  type GroupByOption,
  type MappingLibraryItem,
  type MappingSetStatus,
} from "../utils/mappingsLibraryUtils";

const statusBadgeClass: Record<MappingSetStatus, string> = {
  approved: "bg-emerald-50 text-emerald-700 border-emerald-200",
  in_review: "bg-amber-50 text-amber-700 border-amber-200",
  pending: "bg-gray-100 text-gray-600 border-gray-200",
};

const statusChipActiveClass: Record<MappingSetStatus | "all", string> = {
  all: "border-teal-200 bg-brand-surface text-font-blue",
  approved: "border-emerald-200 bg-emerald-50 text-emerald-700",
  in_review: "border-amber-200 bg-amber-50 text-amber-700",
  pending: "border-gray-200 bg-gray-100 text-gray-600",
};

export default function MappingsLibrary() {
  const navigate = useNavigate();
  const [items, setItems] = useState<MappingLibraryItem[]>(DEMO_MAPPINGS);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [groupBy, setGroupBy] = useState<GroupByOption>("fileType");
  const [statusFilters, setStatusFilters] = useState<Set<MappingSetStatus>>(new Set());
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [selectedItem, setSelectedItem] = useState<MappingLibraryItem | null>(null);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  const showToast = useCallback((message: string) => {
    setToastMessage(message);
    window.setTimeout(() => setToastMessage(null), 2600);
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const sessions = await listHL7Sessions().catch(() => []);
      setItems(mergeLibraryItems(sessions, true));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load mappings library");
      setItems(DEMO_MAPPINGS);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const filteredItems = useMemo(() => {
    const query = search.trim().toLowerCase();
    return items
      .filter((item) => {
        const statusOk = statusFilters.size === 0 || statusFilters.has(item.status);
        const searchOk =
          !query ||
          item.fileType.toLowerCase().includes(query) ||
          item.sender.toLowerCase().includes(query) ||
          item.id.toLowerCase().includes(query);
        return statusOk && searchOk;
      })
      .sort((a, b) => new Date(b.updated).getTime() - new Date(a.updated).getTime());
  }, [items, search, statusFilters]);

  const groupedItems = useMemo(() => {
    const buckets: Record<string, MappingLibraryItem[]> = {};
    filteredItems.forEach((item) => {
      const key = groupKey(item, groupBy);
      if (!buckets[key]) buckets[key] = [];
      buckets[key].push(item);
    });

    const preset = GROUP_ORDER[groupBy];
    const keys = preset
      ? preset.filter((key) => buckets[key]?.length)
      : Object.keys(buckets).sort(
          (a, b) => buckets[b].length - buckets[a].length || a.localeCompare(b),
        );

    return keys.map((key) => ({ key, items: buckets[key] }));
  }, [filteredItems, groupBy]);

  const snapshot = useMemo(() => librarySnapshotStats(items), [items]);

  const toggleStatus = (status: MappingSetStatus | "all") => {
    if (status === "all") {
      setStatusFilters(new Set());
      return;
    }
    setStatusFilters((prev) => {
      const next = new Set(prev);
      if (next.has(status)) next.delete(status);
      else next.add(status);
      return next;
    });
  };

  const toggleGroup = (key: string) => {
    const token = `${key}::${groupBy}`;
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(token)) next.delete(token);
      else next.add(token);
      return next;
    });
  };

  const handleDelete = async (item: MappingLibraryItem) => {
    if (item.isDemo || !item.hl7SessionId) {
      showToast("Demo mapping sets cannot be deleted from the library.");
      return;
    }
    const confirmed = window.confirm(`Remove ${item.fileType} (${item.sender}) from the library?`);
    if (!confirmed) return;
    try {
      await deleteHL7Session(item.hl7SessionId);
      if (selectedItem?.id === item.id) setSelectedItem(null);
      await refresh();
      showToast("Mapping set removed.");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to delete mapping set.");
    }
  };

  const handleExport = async (item: MappingLibraryItem) => {
    if (item.hl7SessionId && !item.isDemo) {
      try {
        await downloadHL7Mappings(item.hl7SessionId);
        showToast("Mapping export started.");
      } catch (err) {
        showToast(err instanceof Error ? err.message : "Failed to export mapping.");
      }
      return;
    }
    showToast(`Would export ${item.fileType} (${item.sender}) as JSON/CSV.`);
  };

  const handleDuplicate = (item: MappingLibraryItem) => {
    if (item.hl7SessionId && !item.isDemo) {
      navigate(sttmNav(`/hl7/${item.hl7SessionId}/review`));
      return;
    }
    showToast("Would duplicate this mapping set into a new editable session.");
  };

  const handleOpenReview = (item: MappingLibraryItem) => {
    if (item.hl7SessionId) {
      navigate(sttmNav(`/hl7/${item.hl7SessionId}/review`));
    }
  };

  return (
    <div className="p-6">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight text-brand-darkblue">Mappings Library</h1>
          <p className="mt-1 max-w-xl text-sm text-gray-500">
            Every approved and in-progress HL7 mapping definition, kept in one place so you can find and reuse them
            instead of starting from scratch.
          </p>
          {!loading && (
            <p className="mt-3 text-xs text-gray-500">
              Library snapshot:{" "}
              <span className="font-semibold text-brand-darkblue">{snapshot.fileTypes} file types</span>
              {" · "}
              <span className="font-semibold text-brand-darkblue">{snapshot.senders} senders</span>
              {" · "}
              <span className="font-semibold text-brand-darkblue">{snapshot.sets} sets</span>
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={() => navigate(sttmNav("/upload"))}
          className="inline-flex items-center gap-2 rounded-lg bg-brand-primary px-3.5 py-2 text-sm font-semibold text-white hover:bg-brand-primary-hover cursor-pointer"
        >
          <Plus size={15} />
          New Mapping
        </button>
      </div>

      <div className="mb-4 rounded-[10px] border border-gray-200 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-end gap-4">
          <div className="relative min-w-[200px] flex-1">
            <Search
              size={15}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400"
            />
            <input
              type="text"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search by file type or sender…"
              className="w-full rounded-full border border-gray-200 bg-brand-light py-2 pl-9 pr-3 text-sm text-brand-charcoal focus:border-teal-200 focus:bg-white focus:outline-none"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <span className="text-[10.5px] font-bold uppercase tracking-[0.06em] text-gray-500">Group by</span>
            <div className="inline-flex rounded-full border border-gray-200 bg-brand-light p-1">
              {(
                [
                  ["fileType", "File Type"],
                  ["sender", "Sender"],
                  ["status", "Status"],
                  ["updated", "Last Updated"],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setGroupBy(value)}
                  className={[
                    "rounded-full px-3 py-1.5 text-[12.5px] font-semibold cursor-pointer",
                    groupBy === value ? "bg-brand-primary text-white" : "text-gray-500 hover:text-brand-charcoal",
                  ].join(" ")}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <span className="text-[10.5px] font-bold uppercase tracking-[0.06em] text-gray-500">Status</span>
            <div className="flex flex-wrap gap-1.5">
              {(
                [
                  ["all", "All"],
                  ["approved", "Approved"],
                  ["in_review", "In review"],
                  ["pending", "Pending"],
                ] as const
              ).map(([value, label]) => {
                const isActive =
                  value === "all" ? statusFilters.size === 0 : statusFilters.has(value as MappingSetStatus);
                return (
                  <button
                    key={value}
                    type="button"
                    onClick={() => toggleStatus(value)}
                    className={[
                      "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1.5 text-xs font-semibold cursor-pointer",
                      isActive
                        ? statusChipActiveClass[value]
                        : "border-gray-200 bg-white text-gray-500 hover:bg-gray-50",
                    ].join(" ")}
                  >
                    <span className="h-1.5 w-1.5 rounded-full bg-current" />
                    {label}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      {loading && <div className="text-sm text-gray-500">Loading mappings library…</div>}
      {error && (
        <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          {error} Showing demo library content.
        </div>
      )}

      {!loading && filteredItems.length > 0 && (
        <p className="mb-3 px-0.5 text-[12.5px] text-gray-500">
          <strong className="text-brand-charcoal">{filteredItems.length}</strong> mapping set
          {filteredItems.length === 1 ? "" : "s"} in{" "}
          <strong className="text-brand-charcoal">{groupedItems.length}</strong> group
          {groupedItems.length === 1 ? "" : "s"}
        </p>
      )}

      {!loading && filteredItems.length === 0 && (
        <div className="rounded-[10px] border border-dashed border-gray-300 bg-white px-5 py-12 text-center text-sm text-gray-500">
          No mapping sets match your filters. Try clearing the search or status filters.
        </div>
      )}

      <div className="space-y-3">
        {groupedItems.map(({ key, items: groupItems }) => {
          const collapseToken = `${key}::${groupBy}`;
          const isCollapsed = collapsedGroups.has(collapseToken);
          return (
            <section key={collapseToken}>
              <button
                type="button"
                onClick={() => toggleGroup(key)}
                className="flex w-full items-center gap-2 rounded-lg px-1 py-2 text-left hover:bg-gray-100 cursor-pointer"
              >
                <ChevronDown
                  size={16}
                  className={["text-gray-500 transition-transform", isCollapsed ? "-rotate-90" : ""].join(" ")}
                />
                <span className="text-[15px] font-extrabold text-brand-darkblue">
                  {groupLabel(key, groupBy)}
                </span>
                <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-bold text-gray-500">
                  {groupItems.length}
                </span>
                <span className="ml-1 h-px flex-1 bg-gray-200" />
              </button>

              {!isCollapsed && (
                <div className="mt-2 space-y-2.5">
                  {groupItems.map((item) => {
                    const currentVersion = item.versions[0];
                    return (
                      <div
                        key={item.id}
                        role="button"
                        tabIndex={0}
                        onClick={() => setSelectedItem(item)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            setSelectedItem(item);
                          }
                        }}
                        className="flex flex-wrap items-center justify-between gap-4 rounded-[10px] border border-gray-200 bg-white px-4 py-4 shadow-sm transition-colors hover:border-teal-200 cursor-pointer"
                      >
                        <div className="min-w-[260px] flex-1">
                          <div className="mb-1 flex flex-wrap items-center gap-2">
                            <span className="text-[15.5px] font-extrabold text-brand-charcoal">{item.fileType}</span>
                            <span
                              className={[
                                "rounded-full border px-2 py-0.5 text-[11px] font-bold capitalize",
                                statusBadgeClass[item.status],
                              ].join(" ")}
                            >
                              {STATUS_LABEL[item.status]}
                            </span>
                            {currentVersion && (
                              <span className="rounded-full border border-indigo-200 bg-indigo-50 px-2 py-0.5 font-mono text-[11px] font-bold text-indigo-700">
                                {currentVersion.version}
                              </span>
                            )}
                          </div>
                          <div className="mb-2 font-mono text-[11.5px] text-gray-500">
                            {item.id} · {item.sender}
                          </div>
                          <div className="flex flex-wrap gap-3.5 text-[12.5px] text-gray-500">
                            <span>{item.messages} message(s)</span>
                            <span>
                              Mappings:{" "}
                              <strong className="text-brand-charcoal">
                                {item.approved}/{item.total}
                              </strong>{" "}
                              approved
                            </span>
                            {item.versions.length > 0 && (
                              <span>
                                {item.versions.length} version{item.versions.length === 1 ? "" : "s"}
                              </span>
                            )}
                            <span>Updated {fmtDate(item.updated)}</span>
                          </div>
                        </div>

                        <div
                          className="flex flex-wrap items-center gap-2"
                          onClick={(event) => event.stopPropagation()}
                          onKeyDown={(event) => event.stopPropagation()}
                        >
                          <button
                            type="button"
                            onClick={() => setSelectedItem(item)}
                            className="rounded-md border border-teal-200 bg-brand-surface px-2.5 py-1.5 text-xs font-semibold text-font-blue hover:bg-teal-100 cursor-pointer"
                          >
                            View Mapping
                          </button>
                          <button
                            type="button"
                            onClick={() => handleExport(item)}
                            className="rounded-md border border-gray-200 px-2.5 py-1.5 text-xs font-semibold text-gray-700 hover:bg-gray-50 cursor-pointer"
                          >
                            Export
                          </button>
                          <button
                            type="button"
                            onClick={() => handleDelete(item)}
                            className="rounded-md border border-red-200 px-2.5 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-50 cursor-pointer"
                          >
                            Delete
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </section>
          );
        })}
      </div>

      <MappingsLibraryDetailModal
        item={selectedItem}
        open={selectedItem != null}
        onClose={() => setSelectedItem(null)}
        onExport={handleExport}
        onDuplicate={handleDuplicate}
        onOpenReview={handleOpenReview}
      />

      {toastMessage && (
        <div className="fixed bottom-5 right-5 z-[80] rounded-lg bg-brand-charcoal px-4 py-3 text-sm font-semibold text-white shadow-lg">
          {toastMessage}
        </div>
      )}
    </div>
  );
}
