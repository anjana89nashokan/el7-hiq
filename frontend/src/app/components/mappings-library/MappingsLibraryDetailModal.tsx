import { useEffect, useMemo, useState } from "react";
import { X } from "lucide-react";
import {
  getHL7Mappings,
  getHL7Version,
  type FieldMapping,
} from "../../end-points/hl7Api";
import {
  STATUS_LABEL,
  fmtDate,
  fieldMappingsToRows,
  packageVersionsToLibraryVersions,
  templateFieldRows,
  type FieldMappingRow,
  type MappingLibraryItem,
  type MappingVersion,
} from "../../utils/mappingsLibraryUtils";

interface MappingsLibraryDetailModalProps {
  item: MappingLibraryItem | null;
  open: boolean;
  onClose: () => void;
  onExport: (item: MappingLibraryItem) => void;
  onDuplicate: (item: MappingLibraryItem) => void;
  onOpenReview: (item: MappingLibraryItem) => void;
}

const statusBadgeClass: Record<string, string> = {
  approved: "bg-emerald-50 text-emerald-700 border-emerald-200",
  in_review: "bg-amber-50 text-amber-700 border-amber-200",
  pending: "bg-gray-100 text-gray-600 border-gray-200",
};

export default function MappingsLibraryDetailModal({
  item,
  open,
  onClose,
  onExport,
  onDuplicate,
  onOpenReview,
}: MappingsLibraryDetailModalProps) {
  const [activeVersionLabel, setActiveVersionLabel] = useState<string | null>(null);
  const [fieldRows, setFieldRows] = useState<FieldMappingRow[]>([]);
  const [versions, setVersions] = useState<MappingVersion[]>(item?.versions ?? []);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [moreCount, setMoreCount] = useState(0);

  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  useEffect(() => {
    if (!item || !open) {
      setActiveVersionLabel(null);
      setFieldRows([]);
      setVersions([]);
      setMoreCount(0);
      return;
    }

    setVersions(item.versions);
    const initialVersion = item.versions[0]?.version ?? null;
    setActiveVersionLabel(initialVersion);

    if (item.isDemo || !item.hl7SessionId) {
      const rows = templateFieldRows(item, item.versions[0] ?? null);
      setFieldRows(rows);
      setMoreCount(Math.max(0, item.total - rows.length));
      return;
    }

    let cancelled = false;
    const loadDetail = async () => {
      setLoadingDetail(true);
      try {
        const response = await getHL7Mappings(item.hl7SessionId!);
        if (cancelled) return;
        const mappedVersions = packageVersionsToLibraryVersions(response.versions, response.mappings.length);
        setVersions(mappedVersions.length ? mappedVersions : item.versions);
        const current = mappedVersions[0] ?? item.versions[0] ?? null;
        setActiveVersionLabel(current?.version ?? null);
        const rows = fieldMappingsToRows(
          response.mappings,
          response.readiness.approved_count,
          response.mappings.length,
        );
        setFieldRows(rows);
        setMoreCount(Math.max(0, response.mappings.length - rows.length));
      } catch {
        if (cancelled) return;
        setVersions(item.versions);
        setActiveVersionLabel(item.versions[0]?.version ?? null);
        const rows = templateFieldRows(item, item.versions[0] ?? null);
        setFieldRows(rows);
        setMoreCount(Math.max(0, item.total - rows.length));
      } finally {
        if (!cancelled) setLoadingDetail(false);
      }
    };

    loadDetail();
    return () => {
      cancelled = true;
    };
  }, [item, open]);

  const activeVersion = useMemo(
    () => versions.find((version) => version.version === activeVersionLabel) ?? versions[0] ?? null,
    [activeVersionLabel, versions],
  );

  const viewingHistorical =
    activeVersion != null && versions.some((version) => version.isCurrent) && !activeVersion.isCurrent;

  const handleViewVersion = async (versionLabel: string) => {
    if (!item) return;
    const versionObj = versions.find((version) => version.version === versionLabel);
    if (!versionObj) return;
    setActiveVersionLabel(versionLabel);

    if (item.hl7SessionId && versionObj.packageVersion != null && !item.isDemo) {
      try {
        const payload = await getHL7Version(item.hl7SessionId, versionObj.packageVersion);
        const mappings = (payload.mappings as FieldMapping[] | undefined) ?? [];
        const approved = mappings.filter((mapping) => mapping.status === "approved").length;
        setFieldRows(fieldMappingsToRows(mappings, approved, mappings.length));
        setMoreCount(Math.max(0, mappings.length - 9));
        return;
      } catch {
        // fall through to template rows
      }
    }

    setFieldRows(templateFieldRows(item, versionObj));
    setMoreCount(Math.max(0, versionObj.total - 9));
  };

  if (!open || !item) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-stretch justify-end bg-[rgba(15,35,42,0.42)]"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      role="presentation"
    >
      <div className="flex h-full w-full max-w-[560px] flex-col bg-white shadow-[-8px_0_30px_rgba(0,0,0,0.15)]">
        <div className="flex items-start justify-between gap-3 border-b border-gray-200 px-5 py-5">
          <div>
            <h2 className="text-lg font-extrabold text-brand-charcoal">
              {item.fileType} · {item.sender}
            </h2>
            <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-gray-500">
              <span className={`rounded-full border px-2 py-0.5 text-[11px] font-bold ${statusBadgeClass[item.status]}`}>
                {STATUS_LABEL[item.status]}
              </span>
              <span>Updated {fmtDate(item.updated)}</span>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gray-100 text-gray-500 hover:bg-gray-200 cursor-pointer"
            aria-label="Close"
          >
            <X size={15} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          <h3 className="mb-2 text-[11px] font-bold uppercase tracking-[0.07em] text-gray-500">Set details</h3>
          <table className="mb-5 w-full border-collapse text-[12.5px]">
            <tbody>
              <tr className="border-b border-gray-100">
                <td className="py-2 pr-2 font-mono text-gray-500">Set ID</td>
                <td className="py-2">{item.id}</td>
              </tr>
              <tr className="border-b border-gray-100">
                <td className="py-2 pr-2 font-mono text-gray-500">Sender</td>
                <td className="py-2">{item.sender}</td>
              </tr>
              <tr className="border-b border-gray-100">
                <td className="py-2 pr-2 font-mono text-gray-500">Messages profiled</td>
                <td className="py-2">{item.messages}</td>
              </tr>
              <tr className="border-b border-gray-100">
                <td className="py-2 pr-2 font-mono text-gray-500">Approval progress</td>
                <td className="py-2">
                  {item.approved} of {item.total} fields approved
                </td>
              </tr>
              {activeVersion && (
                <tr>
                  <td className="py-2 pr-2 font-mono text-gray-500">Current version</td>
                  <td className="py-2">
                    {activeVersion.version} · published {fmtDate(activeVersion.publishedAt)}
                  </td>
                </tr>
              )}
            </tbody>
          </table>

          <h3 className="mb-2 text-[11px] font-bold uppercase tracking-[0.07em] text-gray-500">Version history</h3>
          {versions.length === 0 ? (
            <p className="mb-4 text-xs italic text-gray-500">
              Not yet published — this set is still {STATUS_LABEL[item.status].toLowerCase()}, so it has no version
              history yet.
            </p>
          ) : (
            <div className="mb-4 space-y-2">
              {versions.map((version) => {
                const isActive = version.version === activeVersionLabel;
                return (
                  <div
                    key={version.version}
                    className={[
                      "rounded-lg border px-3 py-2.5",
                      isActive ? "border-teal-200 bg-brand-surface" : "border-gray-200 bg-white",
                    ].join(" ")}
                  >
                    <div className="mb-1 flex flex-wrap items-center gap-2">
                      <span className="font-mono text-[13px] font-extrabold text-indigo-700">{version.version}</span>
                      {version.isCurrent && (
                        <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-bold text-emerald-700">
                          Current
                        </span>
                      )}
                      <span className="text-[12.5px] text-brand-charcoal">{version.summary}</span>
                    </div>
                    <div className="mb-2 flex flex-wrap gap-1 text-[11px] text-gray-500">
                      <span>{fmtDate(version.publishedAt)}</span>
                      <span>·</span>
                      <span>{version.publishedBy}</span>
                      <span>·</span>
                      <span>
                        {version.approved}/{version.total} approved
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      {isActive ? (
                        <span className="text-[11.5px] font-bold text-brand-primary">Viewing</span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => handleViewVersion(version.version)}
                          className="rounded-md border border-gray-200 px-2.5 py-1 text-xs font-semibold text-gray-700 hover:bg-gray-50 cursor-pointer"
                        >
                          View
                        </button>
                      )}
                      {!version.isCurrent && (
                        <button
                          type="button"
                          onClick={() =>
                            window.alert(`Would restore ${version.version} as the new current version.`)
                          }
                          className="rounded-md border border-red-200 px-2.5 py-1 text-xs font-semibold text-red-700 hover:bg-red-50 cursor-pointer"
                        >
                          Restore
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {viewingHistorical && activeVersion && (
            <div className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-teal-200 bg-brand-surface px-3 py-2 text-[12.5px] text-brand-darkblue">
              <span>
                Viewing <strong>{activeVersion.version}</strong> (published {fmtDate(activeVersion.publishedAt)}) —
                read-only history.
              </span>
              <button
                type="button"
                onClick={() => {
                  const current = versions.find((version) => version.isCurrent);
                  if (current) handleViewVersion(current.version);
                }}
                className="rounded-md bg-brand-primary px-2.5 py-1 text-xs font-semibold text-white hover:bg-brand-primary-hover cursor-pointer"
              >
                Back to current
              </button>
            </div>
          )}

          <h3 className="mb-2 text-[11px] font-bold uppercase tracking-[0.07em] text-gray-500">
            Field mappings (sample)
            {activeVersion ? (
              <span className="ml-1 normal-case tracking-normal text-gray-500">— {activeVersion.version}</span>
            ) : null}
          </h3>

          {loadingDetail ? (
            <div className="py-8 text-center text-sm text-gray-500">Loading mapping details…</div>
          ) : (
            <>
              <table className="w-full border-collapse text-[12.5px]">
                <thead>
                  <tr className="border-b border-gray-200 text-left text-[10.5px] uppercase tracking-[0.04em] text-gray-500">
                    <th className="px-2 py-1.5">Source (HL7)</th>
                    <th className="px-2 py-1.5">Target field</th>
                    <th className="px-2 py-1.5">Transform</th>
                    <th className="px-2 py-1.5">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {fieldRows.map((row) => (
                    <tr key={`${row.source}-${row.target}`} className="border-b border-gray-100">
                      <td className="px-2 py-2 font-mono text-[11.5px]">{row.source}</td>
                      <td className="px-2 py-2 font-mono text-[11.5px]">{row.target}</td>
                      <td className="px-2 py-2">{row.transform}</td>
                      <td className="px-2 py-2">
                        <span
                          className={[
                            "inline-flex rounded-full px-2 py-0.5 text-[10.5px] font-bold",
                            row.approved ? "bg-emerald-50 text-emerald-700" : "bg-gray-100 text-gray-600",
                          ].join(" ")}
                        >
                          {row.approved ? "Approved" : "Pending"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {moreCount > 0 && (
                <p className="mt-2 text-xs italic text-gray-500">
                  + {moreCount} more field{moreCount === 1 ? "" : "s"} in the full mapping set (not shown here).
                </p>
              )}
            </>
          )}
        </div>

        <div className="flex flex-wrap gap-2 border-t border-gray-200 px-5 py-4">
          <button
            type="button"
            onClick={() => onExport(item)}
            className="rounded-lg bg-brand-primary px-3.5 py-2 text-sm font-semibold text-white hover:bg-brand-primary-hover cursor-pointer"
          >
            Export Mapping
          </button>
          {item.hl7SessionId && !item.isDemo && (
            <button
              type="button"
              onClick={() => onOpenReview(item)}
              className="rounded-lg border border-teal-200 bg-brand-surface px-3.5 py-2 text-sm font-semibold text-font-blue hover:bg-teal-100 cursor-pointer"
            >
              Open Mapping Review
            </button>
          )}
          <button
            type="button"
            onClick={() => onDuplicate(item)}
            className="rounded-lg border border-teal-200 bg-brand-surface px-3.5 py-2 text-sm font-semibold text-font-blue hover:bg-teal-100 cursor-pointer"
          >
            Duplicate as New Session
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-gray-200 px-3.5 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-50 cursor-pointer"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
