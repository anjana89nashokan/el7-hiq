import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { sttmNav } from "../utils/sttmRoutes";
import {
  bulkApproveHL7Mappings,
  downloadHL7Mappings,
  getCanonicalModel,
  getHL7Mappings,
  getHL7Version,
  publishHL7Package,
  reviewHL7Mapping,
  type CanonicalEntity,
  type FieldMapping,
  type HL7FileResult,
  type MappingStatus,
  type MappingsResponse,
  type PackageVersion,
  type ReviewAction,
  type SavedCustomTarget,
} from "../end-points/hl7Api";
import { hl7Theme as t, PAGE_SIZE_OPTIONS, type PageSizeOption } from "./hl7Theme";

const STATUS_STYLE: Record<MappingStatus, { chip: string; label: string }> = {
  approved: { chip: "border-[#006E74] text-[#006E74] bg-[#F5F5F5]", label: "Approved" },
  proposed: { chip: "border-[#0097AC] text-[#0097AC] bg-white", label: "Proposed" },
  deferred: { chip: "border-[#4A4A4A] text-[#4A4A4A] bg-white", label: "Deferred" },
  rejected: { chip: "border-[#E0E0E0] text-[#4A4A4A] bg-white", label: "Rejected" },
  unresolved: { chip: "border-[#EF9A9A] text-[#212121] bg-white", label: "Unresolved" },
};

type StatusFilterKey = "all" | MappingStatus;

const STATUS_FILTERS: { key: StatusFilterKey; label: string }[] = [
  { key: "all", label: "All statuses" },
  { key: "proposed", label: "Proposed" },
  { key: "approved", label: "Approved" },
  { key: "rejected", label: "Rejected" },
  { key: "deferred", label: "Deferred" },
  { key: "unresolved", label: "Unresolved" },
];

type FilterKey = "needs_action" | "custom" | "low_confidence" | "unresolved" | "approved";

/** Review workbench is Z-segment only; standard HL7 fields are auto-approved at ingest. */
const isReviewableMapping = (m: FieldMapping): boolean => m.category === "custom";

const FILTERS: { key: FilterKey; label: string; hint: string }[] = [
  { key: "needs_action", label: "Needs action", hint: "Site-defined fields awaiting a decision" },
  { key: "custom", label: "All site-defined", hint: "Every Z-segment field in the corpus" },
  { key: "approved", label: "Approved", hint: "Site-defined fields already approved" },
  { key: "low_confidence", label: "Low confidence", hint: "Site-defined, below 0.70" },
  { key: "unresolved", label: "Unresolved", hint: "No target proposed" },
];

const matchesFilter = (m: FieldMapping, f: FilterKey): boolean => {
  switch (f) {
    case "needs_action":
      return m.status === "proposed" || m.status === "unresolved";
    case "custom":
      return true;
    case "approved":
      return m.status === "approved";
    case "low_confidence":
      return m.confidence < 0.7;
    case "unresolved":
      return m.status === "unresolved";
    default:
      return true;
  }
};

const mappingBelongsToFile = (mapping: FieldMapping, file: HL7FileResult): boolean => {
  if (mapping.source_files?.length) {
    return mapping.source_files.includes(file.filename);
  }
  if (file.segments?.length) {
    const seg = mapping.source_segment || mapping.source_path.split("-")[0];
    return file.segments.includes(seg);
  }
  return false;
};

const confidenceTone = (c: number): string =>
  c >= 0.9 ? "bg-[#006E74]" : c >= 0.7 ? "bg-[#0097AC]" : c >= 0.5 ? "bg-[#4A4A4A]" : "bg-[#EF9A9A]";

const sortMappings = (a: FieldMapping, b: FieldMapping): number => {
  const seg = a.source_segment.localeCompare(b.source_segment);
  if (seg !== 0) return seg;
  return a.source_path.localeCompare(b.source_path, undefined, { numeric: true });
};

const pathToExtensionSlug = (path: string): string =>
  path.toLowerCase().replace(/-/g, "_").replace(/\./g, "_");

const isValidExtensionSlug = (slug: string): boolean =>
  /^[a-z][a-z0-9_]*$/i.test(slug);

const extractErrorMessage = (e: unknown, fallback: string): string => {
  if (e instanceof Error && e.message.trim()) return e.message;
  const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) =>
        typeof item === "string" ? item : (item as { msg?: string })?.msg ?? String(item)
      )
      .join(" ");
  }
  return fallback;
};

const ASSIGNMENT_REASON_REQUIRED =
  "A reason is required when assigning or changing a target.";

const REJECT_REASON_REQUIRED = "A reason is required when rejecting a mapping.";

const canBulkApprove = (m: FieldMapping): boolean =>
  isReviewableMapping(m) &&
  Boolean(m.target_path) &&
  (m.status === "proposed" || m.status === "unresolved");

interface FileMappingGroup {
  key: string;
  title: string;
  subtitle?: string;
  mappings: FieldMapping[];
}

const buildFileGroups = (data: MappingsResponse, visible: FieldMapping[]): FileMappingGroup[] => {
  const parsedFiles = (data.files ?? []).filter((f) => f.status === "parsed");
  const groups: FileMappingGroup[] = parsedFiles.map((file) => ({
    key: file.filename,
    title: file.filename,
    subtitle: file.message_type
      ? `${file.message_type} · ${file.segment_count ?? file.segments?.length ?? 0} segments`
      : undefined,
    mappings: visible.filter((m) => mappingBelongsToFile(m, file)).sort(sortMappings),
  }));

  const assigned = new Set(groups.flatMap((g) => g.mappings.map((m) => m.id)));
  const unassigned = visible.filter((m) => !assigned.has(m.id)).sort(sortMappings);
  if (unassigned.length > 0) {
    groups.push({
      key: "__corpus__",
      title: "Corpus-wide",
      subtitle: "Shared across files",
      mappings: unassigned,
    });
  }

  return groups.filter((g) => g.mappings.length > 0);
};

const StatCard: React.FC<{ value: React.ReactNode; label: string; accent?: string }> = ({
  value,
  label,
  accent = "text-[#212121]",
}) => (
  <div className={t.statCard}>
    <div className={`text-3xl font-bold leading-none ${accent}`}>{value}</div>
    <div className="text-xs text-[#4A4A4A] mt-2 uppercase tracking-[0.12em] font-bold">{label}</div>
  </div>
);

interface PaginationProps {
  page: number;
  totalPages: number;
  totalItems: number;
  pageSize: PageSizeOption;
  onPage: (page: number) => void;
  onPageSize: (size: PageSizeOption) => void;
}

const Pagination: React.FC<PaginationProps> = ({
  page,
  totalPages,
  totalItems,
  pageSize,
  onPage,
  onPageSize,
}) => {
  if (totalItems === 0) return null;

  const start = (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, totalItems);

  const pages: number[] = [];
  const windowSize = 5;
  let from = Math.max(1, page - Math.floor(windowSize / 2));
  const to = Math.min(totalPages, from + windowSize - 1);
  from = Math.max(1, to - windowSize + 1);
  for (let i = from; i <= to; i++) pages.push(i);

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3 border-t border-[#E0E0E0] bg-[#F5F5F5]">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-xs text-[#4A4A4A]">
          Showing {start}–{end} of {totalItems}
        </span>
        <label className="flex items-center gap-2 text-xs text-[#4A4A4A]">
          Per page
          <select
            value={pageSize}
            onChange={(e) => onPageSize(Number(e.target.value) as PageSizeOption)}
            className="font-bold border border-[#E0E0E0] bg-white px-2 py-1 text-[#212121] hover:border-[#0097AC]"
          >
            {PAGE_SIZE_OPTIONS.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
      </div>
      {totalPages > 1 && (
        <div className="flex items-center gap-1">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => onPage(page - 1)}
            className="text-xs font-bold px-3 py-1.5 border border-[#E0E0E0] bg-white hover:border-[#0097AC] disabled:opacity-40"
          >
            Prev
          </button>
          {pages.map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => onPage(p)}
              className={`text-xs font-bold min-w-[2rem] px-2 py-1.5 border ${
                p === page
                  ? "bg-[#0097AC] text-white border-[#0097AC]"
                  : "bg-white text-[#212121] border-[#E0E0E0] hover:border-[#0097AC]"
              }`}
            >
              {p}
            </button>
          ))}
          <button
            type="button"
            disabled={page >= totalPages}
            onClick={() => onPage(page + 1)}
            className="text-xs font-bold px-3 py-1.5 border border-[#E0E0E0] bg-white hover:border-[#0097AC] disabled:opacity-40"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
};

type ReviewHandler = (
  mapping: FieldMapping,
  action: ReviewAction,
  extra?: { target_path?: string; transformation?: string; comment?: string }
) => Promise<void>;

interface MappingRowProps {
  mapping: FieldMapping;
  busy: boolean;
  selected: boolean;
  onOpen: () => void;
  onQuickApprove: () => void;
}

const MappingTableColGroup = () => (
  <colgroup>
    <col className="w-[18%]" />
    <col className="w-[28%]" />
    <col className="w-[18%]" />
    <col className="w-[10%]" />
    <col className="w-[10%]" />
    <col className="w-[16%]" />
  </colgroup>
);

const MappingCatalogRow: React.FC<{ mapping: FieldMapping }> = ({ mapping }) => {
  const style = STATUS_STYLE[mapping.status];
  return (
    <tr className={t.tableRow}>
      <td className={t.td}>
        <code className="font-mono text-xs font-bold text-[#212121] block truncate" title={mapping.source_path}>
          {mapping.source_path}
        </code>
        <div className="text-[10px] text-[#0097AC] font-bold uppercase tracking-[0.08em] mt-0.5">
          {mapping.source_segment}
        </div>
      </td>
      <td className={t.td}>
        {mapping.target_path ? (
          <code className="font-mono text-xs text-[#006E74] block truncate" title={mapping.target_path}>
            {mapping.target_path}
          </code>
        ) : (
          <span className="text-xs text-[#212121] italic">No target</span>
        )}
      </td>
      <td className={t.td}>
        <code className="text-[11px] text-[#4A4A4A] block truncate" title={mapping.transformation}>
          {mapping.transformation}
        </code>
      </td>
      <td className={t.td}>
        {mapping.examples[0] ? (
          <code className="text-[11px] text-[#4A4A4A] block truncate" title={mapping.examples[0]}>
            {mapping.examples[0]}
          </code>
        ) : (
          <span className="text-xs text-[#4A4A4A]">—</span>
        )}
      </td>
      <td className={t.tdMiddle}>
        <span
          className={`text-[10px] font-bold px-2 py-1 border whitespace-nowrap ${
            mapping.category === "standard"
              ? "border-[#006E74] text-[#006E74] bg-[#F5F5F5]"
              : "border-[#0097AC] text-[#0097AC] bg-white"
          }`}
        >
          {mapping.category === "standard" ? "Standard · auto" : "Site-defined"}
        </span>
      </td>
      <td className={t.tdMiddle}>
        <span className={`text-[10px] font-bold px-2 py-1 border whitespace-nowrap ${style.chip}`}>
          {style.label}
        </span>
      </td>
    </tr>
  );
};

const CatalogTableColGroup = () => (
  <colgroup>
    <col className="w-[16%]" />
    <col className="w-[26%]" />
    <col className="w-[14%]" />
    <col className="w-[18%]" />
    <col className="w-[14%]" />
    <col className="w-[12%]" />
  </colgroup>
);

type CatalogCategoryFilter = "all" | "standard" | "custom";

const ApprovedMappingsCatalog: React.FC<{ mappings: FieldMapping[] }> = ({ mappings }) => {
  const [categoryFilter, setCategoryFilter] = useState<CatalogCategoryFilter>("all");
  const [segmentFilter, setSegmentFilter] = useState("all");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<PageSizeOption>(10);
  const [collapsed, setCollapsed] = useState(false);

  const segments = useMemo(
    () => [...new Set(mappings.map((m) => m.source_segment))].sort(),
    [mappings]
  );

  const filtered = useMemo(() => {
    let list = mappings;
    if (categoryFilter === "standard") list = list.filter((m) => m.category === "standard");
    if (categoryFilter === "custom") list = list.filter((m) => m.category === "custom");
    if (segmentFilter !== "all") list = list.filter((m) => m.source_segment === segmentFilter);
    return [...list].sort(sortMappings);
  }, [mappings, categoryFilter, segmentFilter]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pageItems = filtered.slice((safePage - 1) * pageSize, safePage * pageSize);

  const standardCount = mappings.filter((m) => m.category === "standard").length;
  const customCount = mappings.filter((m) => m.category === "custom").length;

  useEffect(() => {
    setPage(1);
  }, [categoryFilter, segmentFilter, pageSize]);

  return (
    <section className={`${t.card} overflow-hidden mb-10`}>
      <div className={`${t.cardHeader} flex items-start justify-between gap-4`}>
        <div>
          <div className="font-bold text-[#212121]">Approved mapping catalog</div>
          <div className="text-xs text-[#4A4A4A] mt-1">
            {mappings.length} approved · {standardCount} standard (auto) · {customCount} site-defined
          </div>
        </div>
        <button
          type="button"
          onClick={() => setCollapsed((v) => !v)}
          className="text-[11px] font-bold px-3 py-1.5 border border-[#E0E0E0] bg-white hover:border-[#0097AC] shrink-0"
        >
          {collapsed ? "Show" : "Hide"}
        </button>
      </div>

      {!collapsed && (
        <>
          <div className="px-5 py-3 border-b border-[#E0E0E0] bg-white flex flex-wrap items-center gap-2">
            <span className={t.eyebrow + " shrink-0 mr-1"}>Show</span>
            {(
              [
                ["all", "All approved", mappings.length],
                ["standard", "Standard", standardCount],
                ["custom", "Site-defined", customCount],
              ] as const
            ).map(([key, label, count]) => (
              <button
                key={key}
                type="button"
                onClick={() => setCategoryFilter(key)}
                className={`text-[11px] font-bold px-2.5 py-1 border ${
                  categoryFilter === key
                    ? "bg-[#006E74] text-white border-[#006E74]"
                    : "bg-white text-[#212121] border-[#E0E0E0] hover:border-[#006E74]"
                }`}
              >
                {label}
                <span className="ml-1 opacity-80">{count}</span>
              </button>
            ))}
            {segments.length > 1 && (
              <>
                <span className="text-[#E0E0E0] mx-1">|</span>
                <select
                  value={segmentFilter}
                  onChange={(e) => setSegmentFilter(e.target.value)}
                  className="text-[11px] font-bold border border-[#E0E0E0] bg-white px-2 py-1 text-[#212121]"
                >
                  <option value="all">All segments</option>
                  {segments.map((seg) => (
                    <option key={seg} value={seg}>
                      {seg}
                    </option>
                  ))}
                </select>
              </>
            )}
          </div>

          <div className={t.scrollPanel}>
            {pageItems.length === 0 ? (
              <div className="py-8 text-center text-sm text-[#4A4A4A]">No mappings in this view.</div>
            ) : (
              <table className={t.table}>
                <CatalogTableColGroup />
                <thead className="sticky top-0 z-10">
                  <tr className={t.tableHead}>
                    <th className={t.th}>Source</th>
                    <th className={t.th}>Canonical target</th>
                    <th className={t.th}>Transform</th>
                    <th className={t.th}>Example</th>
                    <th className={t.th}>Kind</th>
                    <th className={t.th}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {pageItems.map((m) => (
                    <MappingCatalogRow key={m.id} mapping={m} />
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <Pagination
            page={safePage}
            totalPages={totalPages}
            totalItems={filtered.length}
            pageSize={pageSize}
            onPage={setPage}
            onPageSize={setPageSize}
          />
        </>
      )}
    </section>
  );
};

const MappingTableRow: React.FC<MappingRowProps> = ({
  mapping,
  busy,
  selected,
  onOpen,
  onQuickApprove,
}) => {
  const style = STATUS_STYLE[mapping.status];
  const decided = mapping.status === "approved" || mapping.status === "rejected";

  return (
    <tr
      className={`${t.tableRow} cursor-pointer ${selected ? "bg-[#F5F5F5] outline outline-1 outline-[#0097AC]" : ""}`}
      onClick={onOpen}
    >
      <td className={t.td}>
        <code className="font-mono text-xs font-bold text-[#212121] block truncate" title={mapping.source_path}>
          {mapping.source_path}
        </code>
        <div className="text-[10px] text-[#0097AC] font-bold uppercase tracking-[0.08em] mt-0.5">
          {mapping.source_segment}
          {mapping.category === "custom" && " · site-defined"}
        </div>
      </td>
      <td className={t.td}>
        {mapping.target_path ? (
          <code className="font-mono text-xs text-[#006E74] block truncate" title={mapping.target_path}>
            {mapping.target_path}
          </code>
        ) : (
          <span className="text-xs text-[#212121] italic">No target</span>
        )}
      </td>
      <td className={t.td}>
        {mapping.examples[0] ? (
          <code className="text-[11px] text-[#4A4A4A] block truncate" title={mapping.examples[0]}>
            {mapping.examples[0]}
          </code>
        ) : (
          <span className="text-xs text-[#4A4A4A]">—</span>
        )}
      </td>
      <td className={t.tdMiddle}>
        <div className="flex items-center gap-2">
          <div className="flex-1 h-1.5 bg-[#E0E0E0] overflow-hidden min-w-0">
            <div
              className={`h-full ${confidenceTone(mapping.confidence)}`}
              style={{ width: `${Math.round(mapping.confidence * 100)}%` }}
            />
          </div>
          <span className="font-mono text-[10px] text-[#212121] w-8 text-right shrink-0">
            {mapping.confidence.toFixed(2)}
          </span>
        </div>
      </td>
      <td className={t.tdMiddle}>
        <span className={`text-[10px] font-bold px-2 py-1 border whitespace-nowrap ${style.chip}`}>
          {style.label}
        </span>
      </td>
      <td className={t.tdMiddle} onClick={(e) => e.stopPropagation()}>
        <div className="flex flex-wrap items-center gap-1">
          <button
            type="button"
            onClick={onOpen}
            className="text-[11px] font-bold px-2 py-1 border border-[#E0E0E0] bg-white hover:border-[#0097AC]"
          >
            Review
          </button>
          {!decided && mapping.target_path && (
            <button
              type="button"
              disabled={busy}
              onClick={onQuickApprove}
              className="text-[11px] font-bold px-2 py-1 bg-[#006E74] text-white hover:bg-[#0097AC] disabled:opacity-40"
            >
              Approve
            </button>
          )}
        </div>
      </td>
    </tr>
  );
};

interface MappingReviewDrawerProps {
  mapping: FieldMapping;
  entities: CanonicalEntity[];
  savedCustomTargets: SavedCustomTarget[];
  busy: boolean;
  index: number;
  total: number;
  onClose: () => void;
  onPrev: () => void;
  onNext: () => void;
  onReview: ReviewHandler;
}

const MappingReviewDrawer: React.FC<MappingReviewDrawerProps> = ({
  mapping,
  entities,
  savedCustomTargets,
  busy,
  index,
  total,
  onClose,
  onPrev,
  onNext,
  onReview,
}) => {
  const [editing, setEditing] = useState(false);
  const [targetMode, setTargetMode] = useState<"existing" | "custom">("existing");
  const [target, setTarget] = useState(mapping.target_path);
  const [customSlug, setCustomSlug] = useState("");
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [commentError, setCommentError] = useState<string | null>(null);

  const suggestedSlug = useMemo(() => pathToExtensionSlug(mapping.source_path), [mapping.source_path]);

  useEffect(() => {
    setTarget(mapping.target_path);
    setCustomSlug(
      mapping.target_path.startsWith("CustomExtension.")
        ? mapping.target_path.slice("CustomExtension.".length)
        : suggestedSlug
    );
    setTargetMode(
      !mapping.target_path || mapping.target_path.startsWith("CustomExtension.")
        ? "custom"
        : "existing"
    );
    setEditing(false);
    setComment("");
    setError(null);
    setCommentError(null);
  }, [mapping.id, mapping.target_path, suggestedSlug]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowLeft" && index > 0) onPrev();
      if (e.key === "ArrowRight" && index < total - 1) onNext();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, onPrev, onNext, index, total]);

  const style = STATUS_STYLE[mapping.status];
  const decided = mapping.status === "approved" || mapping.status === "rejected";

  const targetOptions = useMemo(
    () =>
      entities
        .filter((e) => e.name !== "CustomExtension")
        .flatMap((e) =>
          e.attributes.map((a) => ({
            value: `${e.name}.${a.name}`,
            label: `${e.name}.${a.name}${a.required ? " *" : ""}`,
          }))
        ),
    [entities]
  );

  const savedCustomOptions = useMemo(
    () =>
      savedCustomTargets.map((t) => ({
        value: t.target_path,
        label: t.description
          ? `${t.target_path} — ${t.description}`
          : t.target_path,
      })),
    [savedCustomTargets]
  );

  const effectiveTarget =
    targetMode === "custom"
      ? customSlug.trim()
        ? `CustomExtension.${customSlug.trim()}`
        : ""
      : target;

  const openTargetEditor = () => {
    setEditing(true);
    if (!mapping.target_path) {
      setTargetMode("custom");
      setCustomSlug(suggestedSlug);
    }
  };

  const run = async (
    action: ReviewAction,
    extra?: { target_path?: string; transformation?: string; comment?: string }
  ) => {
    setError(null);
    try {
      await onReview(mapping, action, extra);
      setEditing(false);
      setComment("");
      setCommentError(null);
      if (action === "approve" || action === "edit") {
        if (index < total - 1) onNext();
      }
    } catch (e) {
      setError(extractErrorMessage(e, "Could not record the decision."));
    }
  };

  const saveAssignment = () => {
    if (!effectiveTarget) return;
    if (!comment.trim()) {
      setCommentError(ASSIGNMENT_REASON_REQUIRED);
      setError(null);
      return;
    }
    setCommentError(null);
    void run("edit", { target_path: effectiveTarget, comment: comment.trim() });
  };

  const rejectMapping = () => {
    if (!comment.trim()) {
      setCommentError(REJECT_REASON_REQUIRED);
      setError(null);
      return;
    }
    setCommentError(null);
    void run("reject", { comment: comment.trim() });
  };

  return (
    <>
      <div className={t.drawerOverlay} onClick={onClose} aria-hidden="true" />
      <aside className={t.drawer} role="dialog" aria-modal="true" aria-label="Mapping review">
        <header className="px-5 py-4 border-b border-[#E0E0E0] bg-[#F5F5F5] shrink-0">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className={t.eyebrow + " mb-1"}>Field mapping review</div>
              <code className="font-mono text-lg font-bold text-[#212121] block truncate">
                {mapping.source_path}
              </code>
              <div className="flex flex-wrap items-center gap-2 mt-1">
                <span className="text-[10px] font-bold uppercase tracking-[0.1em] text-[#0097AC]">
                  {mapping.source_segment}
                </span>
                {mapping.category === "custom" && <span className={t.chipZ}>Site-defined</span>}
                <span className={`text-[10px] font-bold px-2 py-0.5 border ${style.chip}`}>
                  {style.label}
                </span>
              </div>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="text-[#4A4A4A] hover:text-[#212121] font-bold text-xl leading-none px-1"
              aria-label="Close panel"
            >
              ×
            </button>
          </div>
          <div className="flex items-center justify-between mt-3">
            <span className="text-[11px] text-[#4A4A4A]">
              {index + 1} of {total} in this view
            </span>
            <div className="flex gap-1">
              <button
                type="button"
                disabled={index <= 0}
                onClick={onPrev}
                className="text-[11px] font-bold px-2 py-1 border border-[#E0E0E0] bg-white hover:border-[#0097AC] disabled:opacity-40"
              >
                ← Prev
              </button>
              <button
                type="button"
                disabled={index >= total - 1}
                onClick={onNext}
                className="text-[11px] font-bold px-2 py-1 border border-[#E0E0E0] bg-white hover:border-[#0097AC] disabled:opacity-40"
              >
                Next →
              </button>
            </div>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {error && !editing && (
            <div className={`${t.errorNote} mb-4`} role="alert">
              {error}
            </div>
          )}

          <section className="mb-6">
            <div className={t.eyebrow + " mb-2"}>Proposed target</div>
            {mapping.target_path ? (
              <>
                <div className="font-mono text-sm text-[#006E74] font-bold">{mapping.target_path}</div>
                <div className="text-xs text-[#4A4A4A] mt-1">
                  {mapping.transformation}
                  {mapping.target_required && " · required"}
                  {mapping.original_target_path && (
                    <span className="text-[#0097AC]"> · overridden from {mapping.original_target_path}</span>
                  )}
                </div>
              </>
            ) : (
              <p className="text-sm text-[#212121] italic">No target proposed — assign one below.</p>
            )}
            <div className="mt-3 flex items-center gap-2">
              <div className="flex-1 h-2 bg-[#E0E0E0] overflow-hidden max-w-[200px]">
                <div
                  className={`h-full ${confidenceTone(mapping.confidence)}`}
                  style={{ width: `${Math.round(mapping.confidence * 100)}%` }}
                />
              </div>
              <span className="font-mono text-xs text-[#212121]">
                Confidence {mapping.confidence.toFixed(2)}
              </span>
            </div>
          </section>

          {mapping.examples.length > 0 && (
            <section className="mb-6">
              <div className={t.eyebrow + " mb-2"}>Example values</div>
              <div className="flex flex-wrap gap-1">
                {mapping.examples.map((v) => (
                  <code key={v} className="text-[11px] bg-[#F5F5F5] px-2 py-1 text-[#212121]">
                    {v}
                  </code>
                ))}
              </div>
            </section>
          )}

          <section className="mb-6">
            <div className={t.eyebrow + " mb-2"}>Evidence</div>
            <ul className="space-y-1.5">
              {mapping.rationale.map((r, i) => (
                <li key={i} className="text-xs text-[#4A4A4A] flex gap-2">
                  <span className="text-[#0097AC] select-none shrink-0">—</span>
                  <span
                    className={
                      r.startsWith("WARNING") || r.startsWith("CARDINALITY")
                        ? "text-[#212121] font-bold"
                        : ""
                    }
                  >
                    {r}
                  </span>
                </li>
              ))}
            </ul>
          </section>

          <section className="mb-6">
            <div className={t.eyebrow + " mb-2"}>Observed</div>
            <div className="text-xs text-[#4A4A4A] space-y-1">
              <div>
                In {mapping.messages_present} message(s), {mapping.occurrences} occurrence(s)
              </div>
              {mapping.stability !== null && <div>Stability {mapping.stability.toFixed(2)}</div>}
              {mapping.target_terminology && <div>Bound to {mapping.target_terminology}</div>}
              {mapping.reviewer && <div>Reviewer: {mapping.reviewer}</div>}
              {mapping.reviewer_comment && (
                <div className="mt-2 pt-2 border-t border-[#E0E0E0] italic">
                  “{mapping.reviewer_comment}”
                </div>
              )}
            </div>
          </section>

          {editing && (
            <section className="mb-6 bg-white border border-[#E0E0E0] border-t-[3px] border-t-[#0097AC] p-4">
              <div className={t.eyebrow + " mb-3"}>Assign target</div>

              <div className="flex gap-2 mb-3">
                <button
                  type="button"
                  onClick={() => setTargetMode("existing")}
                  className={`text-[11px] font-bold px-3 py-1.5 border ${
                    targetMode === "existing"
                      ? "bg-[#0097AC] text-white border-[#0097AC]"
                      : "bg-white text-[#212121] border-[#E0E0E0] hover:border-[#0097AC]"
                  }`}
                >
                  Governed target
                </button>
                <button
                  type="button"
                  onClick={() => setTargetMode("custom")}
                  className={`text-[11px] font-bold px-3 py-1.5 border ${
                    targetMode === "custom"
                      ? "bg-[#006E74] text-white border-[#006E74]"
                      : "bg-white text-[#212121] border-[#E0E0E0] hover:border-[#006E74]"
                  }`}
                >
                  New custom target
                </button>
              </div>

              {targetMode === "existing" ? (
                <select
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                  className="w-full border border-[#E0E0E0] px-2 py-1.5 text-xs font-mono mb-2 bg-white"
                >
                  <option value="">— choose a target —</option>
                  {savedCustomOptions.length > 0 && (
                    <optgroup label="Saved custom targets">
                      {savedCustomOptions.map((o) => (
                        <option key={o.value} value={o.value}>
                          {o.label}
                        </option>
                      ))}
                    </optgroup>
                  )}
                  <optgroup label="Governed canonical targets">
                    {targetOptions.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </optgroup>
                </select>
              ) : (
                <div className="mb-2">
                  <label className="text-[11px] font-bold text-[#4A4A4A] block mb-1">
                    Custom property name
                  </label>
                  <div className="flex items-stretch border border-[#E0E0E0] bg-white">
                    <span className="text-xs font-mono text-[#006E74] px-2 py-1.5 bg-[#F5F5F5] border-r border-[#E0E0E0] shrink-0">
                      CustomExtension.
                    </span>
                    <input
                      type="text"
                      value={customSlug}
                      onChange={(e) =>
                        setCustomSlug(e.target.value.replace(/[^a-zA-Z0-9_]/g, "_"))
                      }
                      placeholder={suggestedSlug}
                      className="flex-1 px-2 py-1.5 text-xs font-mono min-w-0"
                    />
                  </div>
                  {customSlug && !isValidExtensionSlug(customSlug) && (
                    <p className="text-[11px] text-[#212121] mt-1">
                      Use letters, numbers and underscores; start with a letter.
                    </p>
                  )}
                  {effectiveTarget && isValidExtensionSlug(customSlug.trim()) && (
                    <p className="text-[11px] text-[#4A4A4A] mt-1">
                      Will map to{" "}
                      <code className="font-mono text-[#006E74]">{effectiveTarget}</code>
                      {" "}and save it for reuse in this session.
                    </p>
                  )}
                  {savedCustomOptions.length > 0 && (
                    <p className="text-[11px] text-[#4A4A4A] mt-2">
                      Or pick a saved target under{" "}
                      <button
                        type="button"
                        onClick={() => setTargetMode("existing")}
                        className="text-[#006E74] font-bold hover:underline"
                      >
                        Governed target
                      </button>
                      .
                    </p>
                  )}
                </div>
              )}

              <label className="text-[11px] font-bold text-[#212121] block mb-1">
                Assignment reason <span className="text-[#0097AC]">*</span>
              </label>
              <textarea
                value={comment}
                onChange={(e) => {
                  setComment(e.target.value);
                  if (commentError) setCommentError(null);
                  if (error) setError(null);
                }}
                placeholder="Explain why this target is appropriate for this source field."
                className={`w-full border px-2 py-1.5 text-xs mb-1 bg-white ${
                  commentError ? t.fieldError : "border-[#E0E0E0]"
                }`}
                rows={3}
                aria-invalid={commentError ? true : undefined}
                aria-describedby={commentError ? "assignment-reason-error" : undefined}
              />
              {commentError && (
                <div id="assignment-reason-error" className={`${t.errorNote} mb-2`} role="alert">
                  {commentError}
                </div>
              )}
              {error && editing && (
                <div className={`${t.errorNote} mb-2`} role="alert">
                  {error}
                </div>
              )}
              <button
                type="button"
                disabled={
                  busy ||
                  !effectiveTarget ||
                  (targetMode === "custom" && !isValidExtensionSlug(customSlug.trim()))
                }
                onClick={saveAssignment}
                className="text-xs font-bold px-4 py-2 bg-[#0097AC] text-white hover:bg-[#006E74] disabled:opacity-40"
              >
                Save &amp; approve
              </button>
            </section>
          )}
        </div>

        <footer className="px-5 py-4 border-t border-[#E0E0E0] bg-[#F5F5F5] shrink-0">
          {!editing && (commentError || error) && (
            <div className={`${t.errorNote} mb-3`} role="alert">
              {commentError ?? error}
            </div>
          )}
          {!editing && !decided && (
            <div className="mb-3">
              <label className="text-[10px] font-bold uppercase tracking-[0.08em] text-[#006E74] block mb-1">
                Review note (required to reject)
              </label>
              <textarea
                value={comment}
                onChange={(e) => {
                  setComment(e.target.value);
                  if (commentError) setCommentError(null);
                  if (error) setError(null);
                }}
                placeholder="Optional for approve · required when rejecting"
                className={`w-full border px-2 py-1.5 text-xs bg-white ${
                  commentError ? t.fieldError : "border-[#E0E0E0]"
                }`}
                rows={2}
              />
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            {!decided && (
              <>
                <button
                  type="button"
                  disabled={busy || !mapping.target_path}
                  onClick={() => run("approve", { comment: comment || undefined })}
                  className="text-xs font-bold px-4 py-2 bg-[#006E74] text-white hover:bg-[#0097AC] disabled:opacity-40"
                >
                  Approve
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setCommentError(null);
                    editing ? setEditing(false) : openTargetEditor();
                  }}
                  className="text-xs font-bold px-4 py-2 border-2 border-[#006E74] text-[#006E74] bg-white hover:bg-white"
                >
                  {editing ? "Cancel edit" : mapping.target_path ? "Edit target" : "Assign target"}
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => run("defer")}
                  className="text-xs font-bold px-4 py-2 border border-[#E0E0E0] bg-white hover:border-[#0097AC]"
                >
                  Defer
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={rejectMapping}
                  className="text-xs font-bold px-4 py-2 border border-[#EF9A9A] bg-white text-[#212121]"
                >
                  Reject
                </button>
              </>
            )}
            {decided && (
              <button
                type="button"
                disabled={busy}
                onClick={() => run("reset")}
                className="text-xs font-bold px-4 py-2 border border-[#E0E0E0] bg-white hover:border-[#0097AC]"
              >
                Reopen
              </button>
            )}
          </div>
          <p className="text-[10px] text-[#4A4A4A] mt-2">
            ← → navigate · Esc close · Approve advances to next
          </p>
        </footer>
      </aside>
    </>
  );
};

interface WorkbenchProps {
  group: FileMappingGroup;
  busy: boolean;
  segmentFilter: string;
  statusFilter: StatusFilterKey;
  page: number;
  pageSize: PageSizeOption;
  selectedId: string | null;
  onSegmentFilter: (seg: string) => void;
  onStatusFilter: (status: StatusFilterKey) => void;
  onPage: (page: number) => void;
  onPageSize: (size: PageSizeOption) => void;
  onSelect: (id: string) => void;
  onQuickApprove: (mapping: FieldMapping) => void;
  viewApprovableCount: number;
  fileApprovableCount: number;
  onBulkApproveView: () => void;
  onBulkApproveFile: () => void;
}

const MappingWorkbench: React.FC<WorkbenchProps> = ({
  group,
  busy,
  segmentFilter,
  statusFilter,
  page,
  pageSize,
  selectedId,
  onSegmentFilter,
  onStatusFilter,
  onPage,
  onPageSize,
  onSelect,
  onQuickApprove,
  viewApprovableCount,
  fileApprovableCount,
  onBulkApproveView,
  onBulkApproveFile,
}) => {
  const segments = useMemo(() => {
    const names = [...new Set(group.mappings.map((m) => m.source_segment))].sort();
    return names;
  }, [group.mappings]);

  const filtered = useMemo(() => {
    let list = group.mappings;
    if (segmentFilter !== "all") {
      list = list.filter((m) => m.source_segment === segmentFilter);
    }
    if (statusFilter !== "all") {
      list = list.filter((m) => m.status === statusFilter);
    }
    return list;
  }, [group.mappings, segmentFilter, statusFilter]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pageItems = filtered.slice((safePage - 1) * pageSize, safePage * pageSize);

  const pendingInGroup = group.mappings.filter(
    (m) => m.status === "proposed" || m.status === "unresolved"
  ).length;

  return (
    <section className={`${t.card} overflow-hidden`}>
      <div className={t.cardHeader}>
        <div className="font-bold text-[#212121] truncate">{group.title}</div>
        {group.subtitle && (
          <div className="text-xs text-[#4A4A4A] mt-1">
            {group.subtitle} · {group.mappings.length} mapping(s)
            {pendingInGroup > 0 && (
              <span className="text-[#0097AC] font-bold"> · {pendingInGroup} pending</span>
            )}
          </div>
        )}
      </div>

      {segments.length > 0 && (
        <div className={t.chipRow}>
          <button
            type="button"
            onClick={() => onSegmentFilter("all")}
            className={`text-[11px] font-bold px-2.5 py-1 border shrink-0 ${
              segmentFilter === "all"
                ? "bg-[#0097AC] text-white border-[#0097AC]"
                : "bg-white text-[#212121] border-[#E0E0E0] hover:border-[#0097AC]"
            }`}
          >
            All segments
            <span className="ml-1 opacity-80">{group.mappings.length}</span>
          </button>
          {segments.map((seg) => {
            const count = group.mappings.filter((m) => m.source_segment === seg).length;
            const pending = group.mappings.filter(
              (m) =>
                m.source_segment === seg &&
                (m.status === "proposed" || m.status === "unresolved")
            ).length;
            return (
              <button
                key={seg}
                type="button"
                onClick={() => onSegmentFilter(seg)}
                className={`text-[11px] font-bold px-2.5 py-1 border font-mono shrink-0 ${
                  segmentFilter === seg
                    ? "bg-[#0097AC] text-white border-[#0097AC]"
                    : "bg-white text-[#212121] border-[#E0E0E0] hover:border-[#0097AC]"
                }`}
              >
                {seg}
                <span className="ml-1 opacity-80">{count}</span>
                {pending > 0 && segmentFilter !== seg && (
                  <span className="ml-1 text-[#0097AC]">({pending})</span>
                )}
              </button>
            );
          })}
        </div>
      )}

      <div className={`${t.chipRow} border-t-0`}>
        {STATUS_FILTERS.map(({ key, label }) => {
          const count =
            key === "all"
              ? group.mappings.length
              : group.mappings.filter((m) => m.status === key).length;
          if (key !== "all" && count === 0) return null;
          return (
            <button
              key={key}
              type="button"
              onClick={() => onStatusFilter(key)}
              className={`text-[11px] font-bold px-2.5 py-1 border shrink-0 ${
                statusFilter === key
                  ? "bg-[#006E74] text-white border-[#006E74]"
                  : "bg-white text-[#212121] border-[#E0E0E0] hover:border-[#006E74]"
              }`}
            >
              {label}
              <span className="ml-1 opacity-80">{count}</span>
            </button>
          );
        })}
      </div>

      <div className="px-5 py-3 border-b border-[#E0E0E0] bg-white flex flex-wrap items-center gap-2">
        <span className={t.eyebrow + " shrink-0 mr-1"}>Bulk approve site-defined</span>
        <button
          type="button"
          disabled={busy || viewApprovableCount === 0}
          onClick={onBulkApproveView}
          className="text-[11px] font-bold px-3 py-1.5 bg-[#0097AC] text-white hover:bg-[#006E74] disabled:opacity-40"
          title="Approve every mapping in the current filtered table view"
        >
          In this view ({viewApprovableCount})
        </button>
        <button
          type="button"
          disabled={busy || fileApprovableCount === 0}
          onClick={onBulkApproveFile}
          className="text-[11px] font-bold px-3 py-1.5 border-2 border-[#006E74] text-[#006E74] bg-white hover:bg-[#F5F5F5] disabled:opacity-40"
          title="Approve all mappings in this file (ignores segment/status chips)"
        >
          In this file ({fileApprovableCount})
        </button>
      </div>

      <div className={t.scrollPanel}>
        {pageItems.length === 0 ? (
          <div className="py-8 text-center text-sm text-[#4A4A4A]">No mappings in this view.</div>
        ) : (
          <table className={t.table}>
            <MappingTableColGroup />
            <thead className="sticky top-0 z-10">
              <tr className={t.tableHead}>
                <th className={t.th}>Source</th>
                <th className={t.th}>Proposed target</th>
                <th className={t.th}>Example</th>
                <th className={t.th}>Confidence</th>
                <th className={t.th}>Status</th>
                <th className={t.th}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {pageItems.map((m) => (
                <MappingTableRow
                  key={m.id}
                  mapping={m}
                  busy={busy}
                  selected={selectedId === m.id}
                  onOpen={() => onSelect(m.id)}
                  onQuickApprove={() => onQuickApprove(m)}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>

      <Pagination
        page={safePage}
        totalPages={totalPages}
        totalItems={filtered.length}
        pageSize={pageSize}
        onPage={onPage}
        onPageSize={onPageSize}
      />
    </section>
  );
};

const HL7MappingReview: React.FC = () => {
  const { hl7SessionId } = useParams<{ hl7SessionId: string }>();
  const navigate = useNavigate();

  const [data, setData] = useState<MappingsResponse | null>(null);
  const [entities, setEntities] = useState<CanonicalEntity[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterKey>("needs_action");
  const [banner, setBanner] = useState<string | null>(null);
  const [publishError, setPublishError] = useState<string | null>(null);
  const [viewing, setViewing] = useState<Record<string, unknown> | null>(null);

  const [activeFileKey, setActiveFileKey] = useState<string>("");
  const [segmentFilter, setSegmentFilter] = useState<string>("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilterKey>("all");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<PageSizeOption>(15);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  const load = useCallback(async () => {
    if (!hl7SessionId) return;
    setLoading(true);
    try {
      const [mappings, model] = await Promise.all([
        getHL7Mappings(hl7SessionId),
        getCanonicalModel(),
      ]);
      setData(mappings);
      setEntities(model);
      setError(null);
    } catch (e) {
      setError(extractErrorMessage(e, "Could not load the mapping package."));
    } finally {
      setLoading(false);
    }
  }, [hl7SessionId]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleReview = useCallback(
    async (
      mapping: FieldMapping,
      action: ReviewAction,
      extra?: { target_path?: string; transformation?: string; comment?: string }
    ) => {
      if (!hl7SessionId || !data) return;
      setBusy(true);
      try {
        const res = await reviewHL7Mapping(hl7SessionId, {
          mapping_id: mapping.id,
          action,
          ...extra,
        });
        setData({
          ...data,
          mappings: data.mappings.map((m) => (m.id === mapping.id ? res.mapping : m)),
          readiness: res.readiness,
          custom_targets: res.custom_targets ?? data.custom_targets,
        });
      setPublishError(null);
    } catch (e) {
      throw e;
    } finally {
      setBusy(false);
    }
    },
    [hl7SessionId, data]
  );

  const handleBulkApproveIds = async (ids: string[], scopeLabel: string) => {
    if (!hl7SessionId || !data || ids.length === 0) return;
    setBusy(true);
    try {
      const res = await bulkApproveHL7Mappings(hl7SessionId, { mapping_ids: ids });
      setData({ ...data, mappings: res.mappings, readiness: res.readiness });
      const skippedNote =
        res.skipped > 0 ? ` ${res.skipped} skipped (no target or already decided).` : "";
      setBanner(`Approved ${res.approved} mapping(s) — ${scopeLabel}.${skippedNote}`);
      setPublishError(null);
    } catch (e) {
      setBanner(extractErrorMessage(e, "Could not bulk-approve mappings."));
    } finally {
      setBusy(false);
    }
  };

  const handlePublish = async () => {
    if (!hl7SessionId) return;
    setBusy(true);
    setPublishError(null);
    try {
      const res = await publishHL7Package(hl7SessionId);
      setBanner(
        `Published version ${(res.package as { version: number }).version} with ` +
          `${(res.package as { mapping_count: number }).mapping_count} mappings. ` +
          `This version is immutable — further changes create a new one.`
      );
      await load();
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setPublishError(detail ?? "Could not publish the package.");
    } finally {
      setBusy(false);
    }
  };

  const handleDownload = async () => {
    if (!hl7SessionId) return;
    setDownloading(true);
    try {
      await downloadHL7Mappings(hl7SessionId);
    } catch {
      setBanner("Could not download the mapping package.");
    } finally {
      setDownloading(false);
    }
  };

  const establishedMappings = useMemo(
    () => (data ? data.mappings.filter((m) => m.status === "approved") : []),
    [data]
  );

  const reviewMappings = useMemo(
    () => (data ? data.mappings.filter(isReviewableMapping) : []),
    [data]
  );

  const standardStats = useMemo(() => {
    if (!data) return { total: 0, approved: 0 };
    const standard = data.mappings.filter((m) => m.category === "standard");
    return {
      total: standard.length,
      approved: standard.filter((m) => m.status === "approved").length,
    };
  }, [data]);

  const visible = useMemo(
    () => reviewMappings.filter((m) => matchesFilter(m, filter)),
    [reviewMappings, filter]
  );

  const fileGroups = useMemo(
    () => (data ? buildFileGroups(data, visible) : []),
    [data, visible]
  );

  const activeGroup = fileGroups.find((g) => g.key === activeFileKey) ?? null;

  const filteredMappings = useMemo(() => {
    if (!activeGroup) return [];
    let list = activeGroup.mappings;
    if (segmentFilter !== "all") {
      list = list.filter((m) => m.source_segment === segmentFilter);
    }
    if (statusFilter !== "all") {
      list = list.filter((m) => m.status === statusFilter);
    }
    return list;
  }, [activeGroup, segmentFilter, statusFilter]);

  const viewApprovableIds = useMemo(
    () => filteredMappings.filter(canBulkApprove).map((m) => m.id),
    [filteredMappings]
  );

  const fileApprovableIds = useMemo(
    () => (activeGroup ? activeGroup.mappings.filter(canBulkApprove).map((m) => m.id) : []),
    [activeGroup]
  );

  const sessionApprovableIds = useMemo(
    () => visible.filter(canBulkApprove).map((m) => m.id),
    [visible]
  );

  const selectedMapping = useMemo(() => {
    if (!selectedId || !data) return null;
    return data.mappings.find((m) => m.id === selectedId) ?? null;
  }, [selectedId, data]);

  const selectedIndex = selectedMapping
    ? filteredMappings.findIndex((m) => m.id === selectedMapping.id)
    : -1;

  useEffect(() => {
    if (fileGroups.length === 0) {
      setActiveFileKey("");
      return;
    }
    if (!activeFileKey || !fileGroups.some((g) => g.key === activeFileKey)) {
      setActiveFileKey(fileGroups[0].key);
    }
  }, [fileGroups, activeFileKey]);

  useEffect(() => {
    setPage(1);
    setSegmentFilter("all");
    setStatusFilter("all");
    setSelectedId(null);
  }, [activeFileKey, filter]);

  useEffect(() => {
    setPage(1);
  }, [segmentFilter, statusFilter, pageSize]);

  useEffect(() => {
    if (selectedId && !filteredMappings.some((m) => m.id === selectedId)) {
      setSelectedId(null);
    }
  }, [selectedId, filteredMappings]);

  const navigateDrawer = (delta: number) => {
    if (selectedIndex < 0) return;
    const next = filteredMappings[selectedIndex + delta];
    if (next) setSelectedId(next.id);
  };

  const counts = useMemo(() => {
    const pending = reviewMappings.filter(
      (m) => m.status === "proposed" || m.status === "unresolved"
    ).length;
    const approved = reviewMappings.filter((m) => m.status === "approved").length;
    return {
      reviewTotal: reviewMappings.length,
      reviewApproved: approved,
      reviewPending: pending,
    };
  }, [reviewMappings]);

  if (loading) {
    return (
      <div className={`${t.page} flex items-center justify-center`}>
        <div className="font-bold text-[#4A4A4A]">Loading mapping package…</div>
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className={`${t.page} flex items-center justify-center`}>
        <div className="text-center">
          <div className="text-[#212121] font-bold mb-4">
            {error ?? "No mapping package available."}
          </div>
          <button type="button" onClick={() => navigate(sttmNav("/upload"))} className={t.btnOutline}>
            Back to upload
          </button>
        </div>
      </div>
    );
  }

  const { readiness, versions } = data;

  return (
    <div className={t.page}>
      <main className={t.container}>
        <div className="flex items-start justify-between mb-8 gap-4">
          <div>
            <div className={t.eyebrow}>HL7 v2 · mapping review</div>
            <h2 className={t.heading}>Mapping review</h2>
            <div className={t.accentRule} />
            <p className={`${t.subtext} max-w-3xl`}>
              Standard segments are auto-mapped and listed in the approved catalog below.
              Site-defined Z-segment fields appear in the review queue when they need a decision.
            </p>
          </div>
          <button
            type="button"
            onClick={() => navigate(sttmNav(`/hl7/${hl7SessionId}`))}
            className={t.btnOutline}
          >
            Back to profile
          </button>
        </div>

        {banner && (
          <div className="mb-6 flex items-start justify-between gap-4 bg-[#F5F5F5] border-l-4 border-[#006E74] px-4 py-3 text-sm text-[#212121]">
            <span>{banner}</span>
            <button type="button" onClick={() => setBanner(null)} className="font-bold shrink-0">
              ×
            </button>
          </div>
        )}

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
          <StatCard
            value={
              standardStats.total > 0
                ? `${standardStats.approved}/${standardStats.total}`
                : "—"
            }
            label="Standard fields (auto)"
            accent="text-[#006E74]"
          />
          <StatCard
            value={
              standardStats.total > 0
                ? `${Math.round((standardStats.approved / standardStats.total) * 100)}%`
                : "—"
            }
            label="Standard completion"
            accent="text-[#006E74]"
          />
          <StatCard
            value={counts.reviewPending}
            label="Z-segments awaiting"
            accent={counts.reviewPending ? "text-[#0097AC]" : "text-[#006E74]"}
          />
          <StatCard
            value={`${counts.reviewApproved}/${counts.reviewTotal || 0}`}
            label="Z-segments approved"
            accent="text-[#212121]"
          />
        </div>

        <section className={`${t.card} mb-8 p-6`}>
          <div className="flex items-start justify-between gap-6 flex-wrap">
            <div className="flex-1 min-w-[300px]">
              <h3 className="font-bold text-[#212121] mb-1">Publication</h3>
              {readiness.can_publish ? (
                <p className="text-sm text-[#006E74]">
                  Every required canonical attribute has an approved mapping. The package can be
                  published as an immutable version.
                </p>
              ) : (
                <>
                  <p className="text-sm text-[#4A4A4A] mb-3">
                    Publication is blocked while a required canonical attribute has no approved
                    mapping.
                  </p>
                  <ul className="space-y-1.5 max-h-32 overflow-y-auto">
                    {readiness.blockers.map((b) => (
                      <li key={b.target_path} className="text-xs flex gap-2">
                        <span className="text-[#0097AC] font-bold select-none">!</span>
                        <span>
                          <span className="font-mono text-[#212121]">{b.target_path}</span>
                          <span className="text-[#4A4A4A]"> — {b.reason}</span>
                        </span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {publishError && (
                <div className="mt-3 text-xs text-[#212121] bg-[#F5F5F5] border-l-4 border-[#EF9A9A] px-3 py-2">
                  {publishError}
                </div>
              )}
            </div>

            <div className="flex flex-col gap-2 min-w-[200px]">
              <button
                type="button"
                disabled={downloading}
                onClick={() => void handleDownload()}
                className={`${t.btnOutline} disabled:opacity-40 text-center text-xs px-4 py-2`}
              >
                {downloading ? "Preparing…" : "Download mappings (JSON)"}
              </button>
              <button
                type="button"
                disabled={busy || sessionApprovableIds.length === 0}
                onClick={() =>
                  void handleBulkApproveIds(sessionApprovableIds, "all site-defined fields in session")
                }
                className={`${t.btnPrimary} disabled:opacity-40 text-center text-xs px-4 py-2`}
              >
                Approve all Z-segments ({sessionApprovableIds.length})
              </button>
              <button
                type="button"
                disabled={busy || !readiness.can_publish}
                onClick={handlePublish}
                className={`${t.btnPrimary} disabled:opacity-40 text-center text-xs px-4 py-2`}
              >
                Publish v{(versions[versions.length - 1]?.version ?? 0) + 1}
              </button>
              <div className="text-[11px] text-[#4A4A4A] text-center">
                {readiness.approved_count} of {readiness.total_count} approved overall
              </div>
            </div>
          </div>

          {versions.length > 0 && (
            <div className="mt-5 pt-5 border-t border-[#E0E0E0]">
              <div className={t.eyebrow + " mb-2"}>Published versions</div>
              <div className="flex flex-wrap gap-2">
                {versions.map((v: PackageVersion) => (
                  <button
                    key={v.version}
                    type="button"
                    onClick={async () =>
                      setViewing(await getHL7Version(hl7SessionId!, v.version))
                    }
                    className="text-xs border border-[#E0E0E0] bg-white px-3 py-2 hover:border-[#0097AC] text-left"
                  >
                    <div className="font-bold text-[#212121]">v{v.version}</div>
                    <div className="text-[11px] text-[#4A4A4A]">
                      {v.mapping_count} mappings · {v.published_by}
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}
        </section>

        {establishedMappings.length > 0 && (
          <ApprovedMappingsCatalog mappings={establishedMappings} />
        )}

        <div className="mb-4">
          <h3 className="font-bold text-[#212121] text-sm uppercase tracking-[0.08em]">
            Site-defined review queue
          </h3>
          <p className="text-xs text-[#4A4A4A] mt-1">
            Z-segment fields that still need approval, rejection, or a target assignment.
          </p>
        </div>

        <div className="flex flex-wrap gap-2 mb-4">
          {FILTERS.map((f) => {
            const count = reviewMappings.filter((m) => matchesFilter(m, f.key)).length;
            return (
              <button
                key={f.key}
                type="button"
                title={f.hint}
                onClick={() => setFilter(f.key)}
                className={`text-xs font-bold px-3 py-1.5 border transition ${
                  filter === f.key
                    ? "bg-[#0097AC] text-white border-[#0097AC]"
                    : "bg-white text-[#212121] border-[#E0E0E0] hover:border-[#0097AC]"
                }`}
              >
                {f.label}
                <span className={`ml-1.5 ${filter === f.key ? "text-white/80" : "text-[#4A4A4A]"}`}>
                  {count}
                </span>
              </button>
            );
          })}
        </div>

        {fileGroups.length === 0 ? (
          <div className={`${t.card} p-10 text-center text-sm text-[#4A4A4A]`}>
            {counts.reviewTotal === 0
              ? "No site-defined Z-segment fields in this upload. Standard segments were auto-approved — you can publish when ready."
              : filter === "needs_action"
                ? "All Z-segment fields are decided. Standard segments remain auto-approved in the background."
                : "Nothing matches this filter."}
          </div>
        ) : (
          <>
            {fileGroups.length > 1 && (
              <div className="mb-4 flex flex-wrap gap-0 border border-[#E0E0E0] bg-white">
                {fileGroups.map((g) => {
                  const pending = g.mappings.filter(
                    (m) => m.status === "proposed" || m.status === "unresolved"
                  ).length;
                  const active = g.key === activeFileKey;
                  return (
                    <button
                      key={g.key}
                      type="button"
                      onClick={() => setActiveFileKey(g.key)}
                      className={`text-xs font-bold px-4 py-3 border-r border-[#E0E0E0] last:border-r-0 transition ${
                        active
                          ? "bg-[#0097AC] text-white border-b-[3px] border-b-[#006E74]"
                          : "bg-white text-[#212121] hover:bg-[#F5F5F5] border-b-[3px] border-b-transparent"
                      }`}
                      title={g.subtitle}
                    >
                      <span className="block truncate max-w-[180px]">{g.title}</span>
                      <span
                        className={`text-[10px] font-normal ${active ? "text-white/80" : "text-[#4A4A4A]"}`}
                      >
                        {g.mappings.length} fields
                        {pending > 0 && ` · ${pending} pending`}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}

            {activeGroup && (
              <MappingWorkbench
                group={activeGroup}
                busy={busy}
                segmentFilter={segmentFilter}
                statusFilter={statusFilter}
                page={page}
                pageSize={pageSize}
                selectedId={selectedId}
                onSegmentFilter={setSegmentFilter}
                onStatusFilter={setStatusFilter}
                onPage={setPage}
                onPageSize={setPageSize}
                onSelect={setSelectedId}
                onQuickApprove={(m) => void handleReview(m, "approve")}
                viewApprovableCount={viewApprovableIds.length}
                fileApprovableCount={fileApprovableIds.length}
                onBulkApproveView={() =>
                  void handleBulkApproveIds(viewApprovableIds, "current filtered view")
                }
                onBulkApproveFile={() =>
                  void handleBulkApproveIds(fileApprovableIds, "current file")
                }
              />
            )}
          </>
        )}
      </main>

      {selectedMapping && selectedIndex >= 0 && (
        <MappingReviewDrawer
          mapping={selectedMapping}
          entities={entities}
          savedCustomTargets={data.custom_targets ?? []}
          busy={busy}
          index={selectedIndex}
          total={filteredMappings.length}
          onClose={() => setSelectedId(null)}
          onPrev={() => navigateDrawer(-1)}
          onNext={() => navigateDrawer(1)}
          onReview={handleReview}
        />
      )}

      {viewing && (
        <div
          className="fixed inset-0 bg-black/40 flex items-center justify-center p-8 z-50"
          onClick={() => setViewing(null)}
        >
          <div
            className="bg-white max-w-4xl w-full max-h-[80vh] overflow-auto border border-[#E0E0E0] border-t-[3px] border-t-[#0097AC]"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-6 py-4 border-b border-[#E0E0E0] sticky top-0 bg-white">
              <h3 className="font-bold text-[#212121]">
                Mapping package v{(viewing as { version: number }).version}
              </h3>
              <button type="button" onClick={() => setViewing(null)} className="text-[#4A4A4A] text-xl">
                ×
              </button>
            </div>
            <pre className="p-6 text-xs font-mono text-[#4A4A4A] whitespace-pre-wrap">
              {JSON.stringify(viewing, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
};

export default HL7MappingReview;
