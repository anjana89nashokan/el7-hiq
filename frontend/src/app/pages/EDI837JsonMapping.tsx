import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { getHL7Session } from "../end-points/hl7Api";
import { getEdiDecoded, type HL7ResultWithEdi } from "../end-points/ediApi";
import { sttmNav } from "../utils/sttmRoutes";
import { hl7Theme as t, PAGE_SIZE_OPTIONS, type PageSizeOption } from "./hl7Theme";
import { buildEdi837MappingRows, type Edi837MappingRow } from "../utils/edi837MappingRows";
import {
  getRowConfig,
  loadEdi837MappingPackage,
  mergeMappingRows,
  saveEdi837MappingPackage,
  type Edi837MappingPackage,
} from "../utils/edi837MappingStorage";
import {
  applyEdi837Transform,
  buildExportTransformMap,
  classifyEdi837Temporal,
  effectiveFormatId,
  formatLabel,
  formatOptionsForKind,
  previewMappingValue,
  suggestDateTimeFormat,
  transformModeLabel,
  type Edi837DateTimeFormatId,
  type Edi837RowMappingConfig,
  type Edi837TransformMode,
} from "../utils/edi837Transforms";
import { download837Json } from "../utils/edi837ExportJson";

const STATUS_STYLE = {
  approved: { chip: "border-[#006E74] text-[#006E74] bg-[#F5F5F5]", label: "Approved" },
  pending: { chip: "border-[#0097AC] text-[#0097AC] bg-white", label: "Pending" },
} as const;

const JsonMappingColGroup = () => (
  <colgroup>
    <col style={{ width: "18%" }} />
    <col style={{ width: "28%" }} />
    <col style={{ width: "14%" }} />
    <col style={{ width: "6%" }} />
    <col style={{ width: "6%" }} />
    <col style={{ width: "9%" }} />
    <col style={{ width: "13%" }} />
  </colgroup>
);

const compactSampleClass =
  "text-[10px] font-mono text-[#4A4A4A] truncate block max-w-full";

const EDI837JsonMapping: React.FC = () => {
  const { hl7SessionId } = useParams<{ hl7SessionId: string }>();
  const navigate = useNavigate();

  const [result, setResult] = useState<HL7ResultWithEdi | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeFile, setActiveFile] = useState("");
  const [sectionFilter, setSectionFilter] = useState("all");
  const [segmentFilter, setSegmentFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState<"all" | "pending" | "approved">("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<PageSizeOption>(25);
  const [pkg, setPkg] = useState<Edi837MappingPackage>(() => emptyPkg());
  const [selectedRowId, setSelectedRowId] = useState<string | null>(null);
  const [drawerMode, setDrawerMode] = useState<Edi837TransformMode>("transform");
  const [drawerFormat, setDrawerFormat] = useState<Edi837DateTimeFormatId>("date_ccyymmdd_iso");

  function emptyPkg(): Edi837MappingPackage {
    return { version: 2, rows: {} };
  }

  const persist = useCallback(
    (next: Edi837MappingPackage) => {
      if (!hl7SessionId) return;
      setPkg(next);
      saveEdi837MappingPackage(hl7SessionId, next);
    },
    [hl7SessionId]
  );

  const updateRow = useCallback(
    (rowId: string, patch: Partial<Edi837RowMappingConfig>) => {
      const row = pkg.rows[rowId];
      if (!row) return;
      persist({
        version: 2,
        rows: { ...pkg.rows, [rowId]: { ...row, ...patch } },
      });
    },
    [pkg, persist]
  );

  useEffect(() => {
    if (!hl7SessionId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const session = await getHL7Session(hl7SessionId);
        if ((session as HL7ResultWithEdi).view_mode !== "837_decode") {
          if (!cancelled) navigate(sttmNav(`/hl7/${hl7SessionId}`), { replace: true });
          return;
        }
        let withEdi = session as HL7ResultWithEdi;
        if (!withEdi.edi_decoded) {
          withEdi = { ...withEdi, edi_decoded: await getEdiDecoded(hl7SessionId) };
        }
        if (!cancelled) {
          setResult(withEdi);
          setActiveFile(withEdi.edi_decoded?.files[0]?.filename ?? "");
          setPkg(loadEdi837MappingPackage(hl7SessionId));
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Failed to load session");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [hl7SessionId, navigate]);

  const decoded = result?.edi_decoded;
  const files = decoded?.files ?? [];

  const allRows = useMemo(() => {
    if (!decoded || !activeFile) return [];
    return buildEdi837MappingRows(decoded, activeFile);
  }, [decoded, activeFile]);

  useEffect(() => {
    if (!hl7SessionId || allRows.length === 0) return;
    setPkg((current) => {
      const merged = mergeMappingRows(current, allRows);
      if (merged.rows === current.rows) return current;
      saveEdi837MappingPackage(hl7SessionId, merged);
      return merged;
    });
  }, [hl7SessionId, allRows]);

  const selectedRow = useMemo(
    () => allRows.find((r) => r.id === selectedRowId) ?? null,
    [allRows, selectedRowId]
  );

  useEffect(() => {
    if (!selectedRow) return;
    const config = getRowConfig(pkg, selectedRow);
    const kind = classifyEdi837Temporal(selectedRow);
    if (!kind) return;
    setDrawerMode(config.mode);
    setDrawerFormat(config.formatId ?? suggestDateTimeFormat(selectedRow, kind));
  }, [selectedRow, pkg]);

  const sections = useMemo(() => {
    const ids = new Map<string, string>();
    for (const row of allRows) ids.set(row.section_id, row.section_title);
    return [...ids.entries()].map(([id, title]) => ({ id, title }));
  }, [allRows]);

  const segments = useMemo(() => [...new Set(allRows.map((r) => r.segment_id))].sort(), [allRows]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return allRows.filter((row) => {
      const config = getRowConfig(pkg, row);
      if (sectionFilter !== "all" && row.section_id !== sectionFilter) return false;
      if (segmentFilter !== "all" && row.segment_id !== segmentFilter) return false;
      if (statusFilter !== "all" && config.status !== statusFilter) return false;
      if (!q) return true;
      return (
        row.element_id.toLowerCase().includes(q) ||
        row.element_name.toLowerCase().includes(q) ||
        row.target.toLowerCase().includes(q) ||
        row.segment_id.toLowerCase().includes(q)
      );
    });
  }, [allRows, pkg, sectionFilter, segmentFilter, statusFilter, search]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pageRows = filtered.slice((safePage - 1) * pageSize, safePage * pageSize);

  useEffect(() => {
    setPage(1);
  }, [sectionFilter, segmentFilter, statusFilter, search, activeFile, pageSize]);

  const counts = useMemo(() => {
    let approved = 0;
    let pending = 0;
    for (const row of allRows) {
      const s = getRowConfig(pkg, row).status;
      if (s === "approved") approved += 1;
      else pending += 1;
    }
    return { approved, pending, total: allRows.length };
  }, [allRows, pkg]);

  const temporalPending = useMemo(
    () =>
      allRows.filter((row) => {
        if (!classifyEdi837Temporal(row)) return false;
        return getRowConfig(pkg, row).status !== "approved";
      }).length,
    [allRows, pkg]
  );

  const approveRow = (row: Edi837MappingRow) => {
    updateRow(row.id, { status: "approved" });
  };

  const approveAll = () => {
    const rows = { ...pkg.rows };
    for (const row of allRows) {
      rows[row.id] = { ...getRowConfig(pkg, row), status: "approved" };
    }
    persist({ version: 2, rows });
  };

  const openEdit = (row: Edi837MappingRow) => {
    if (!classifyEdi837Temporal(row)) return;
    setSelectedRowId(row.id);
  };

  const saveDrawer = () => {
    if (!selectedRow) return;
    updateRow(selectedRow.id, {
      mode: drawerMode,
      formatId: drawerMode === "transform" ? drawerFormat : undefined,
      status: "approved",
    });
    setSelectedRowId(null);
  };

  if (loading) {
    return (
      <div className={`${t.page} flex items-center justify-center`}>
        <div className="font-bold text-[#4A4A4A]">Loading JSON mapping…</div>
      </div>
    );
  }

  if (error || !result || !decoded) {
    return (
      <div className={`${t.page} flex items-center justify-center`}>
        <div className="text-[#212121] font-bold">{error ?? "No decode data for this session."}</div>
      </div>
    );
  }

  const exportTransforms = buildExportTransformMap(allRows, pkg.rows);

  return (
    <div className={t.page}>
      <main className={`${t.container} max-w-[1400px]`}>
        <div className="flex items-start justify-between mb-6 gap-4">
          <div>
            <div className={t.eyebrow}>X12 837 · JSON mapping</div>
            <h2 className={t.heading}>Map to JSON</h2>
            <div className={t.accentRule} />
            <p className={t.subtext}>
              Non date/time fields are always Direct (raw X12). Date and time fields use Direct or
              Transform with a chosen format — review and approve like HL7 mapping.
            </p>
          </div>
          <div className="flex flex-col gap-2 shrink-0 items-end">
            <button
              type="button"
              className={t.btnPrimary}
              disabled={temporalPending > 0}
              title={
                temporalPending > 0
                  ? `Approve ${temporalPending} date/time field(s) before download`
                  : undefined
              }
              onClick={() => download837Json(decoded, activeFile, exportTransforms)}
            >
              Download JSON
            </button>
            <button
              type="button"
              className={t.btnOutline}
              onClick={() => navigate(sttmNav(`/hl7/${hl7SessionId}`))}
            >
              ← Back to decode
            </button>
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-6">
          <div className={t.statCard}>
            <div className="text-2xl font-bold text-[#006E74]">
              {counts.approved}/{counts.total}
            </div>
            <div className="text-xs text-[#4A4A4A] mt-1">Fields approved</div>
          </div>
          <div className={t.statCard}>
            <div className={`text-2xl font-bold ${temporalPending ? "text-[#0097AC]" : "text-[#006E74]"}`}>
              {temporalPending}
            </div>
            <div className="text-xs text-[#4A4A4A] mt-1">Date/time awaiting approval</div>
          </div>
          <div className={t.statCard}>
            <button
              type="button"
              onClick={approveAll}
              className="text-sm font-bold px-4 py-2 bg-[#006E74] text-white hover:bg-[#0097AC] w-full"
            >
              Approve all
            </button>
          </div>
        </div>

        {files.length > 1 && (
          <div className="mb-4 flex flex-wrap gap-0 border border-[#E0E0E0] bg-white overflow-x-auto">
            {files.map((file) => {
              const selected = file.filename === activeFile;
              return (
                <button
                  key={file.filename}
                  type="button"
                  onClick={() => setActiveFile(file.filename)}
                  className={`text-xs font-bold px-4 py-3 border-r border-[#E0E0E0] last:border-r-0 transition shrink-0 ${
                    selected
                      ? "bg-[#0097AC] text-white border-b-[3px] border-b-[#006E74]"
                      : "bg-white text-[#212121] hover:bg-[#F5F5F5] border-b-[3px] border-b-transparent"
                  }`}
                >
                  {file.filename}
                </button>
              );
            })}
          </div>
        )}

        <div className={`${t.card} mb-4 p-4 flex flex-wrap items-center gap-3`}>
          <label className="text-[11px] font-bold uppercase tracking-wider text-[#4A4A4A]">
            Section
            <select
              value={sectionFilter}
              onChange={(e) => setSectionFilter(e.target.value)}
              className="ml-2 text-[11px] font-bold border border-[#E0E0E0] bg-white px-2 py-1"
            >
              <option value="all">All</option>
              {sections.map((s) => (
                <option key={s.id} value={s.id}>{s.title}</option>
              ))}
            </select>
          </label>
          <label className="text-[11px] font-bold uppercase tracking-wider text-[#4A4A4A]">
            Segment
            <select
              value={segmentFilter}
              onChange={(e) => setSegmentFilter(e.target.value)}
              className="ml-2 text-[11px] font-bold border border-[#E0E0E0] bg-white px-2 py-1"
            >
              <option value="all">All</option>
              {segments.map((seg) => (
                <option key={seg} value={seg}>{seg}</option>
              ))}
            </select>
          </label>
          <label className="text-[11px] font-bold uppercase tracking-wider text-[#4A4A4A]">
            Status
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as typeof statusFilter)}
              className="ml-2 text-[11px] font-bold border border-[#E0E0E0] bg-white px-2 py-1"
            >
              <option value="all">All</option>
              <option value="pending">Pending</option>
              <option value="approved">Approved</option>
            </select>
          </label>
          <input
            type="search"
            placeholder="Search…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="text-sm border border-[#E0E0E0] px-3 py-1.5 min-w-[160px] flex-1"
          />
        </div>

        <section className={t.card}>
          <div className={t.scrollPanel}>
            <table className="w-full min-w-[1040px] table-fixed border-collapse text-sm">
              <JsonMappingColGroup />
              <thead className="sticky top-0 z-10">
                <tr className={t.tableHead}>
                  <th className={t.th}>Source</th>
                  <th className={t.th}>JSON target</th>
                  <th className={t.th}>Transform</th>
                  <th className={`${t.th} px-2`}>Ex.</th>
                  <th className={`${t.th} px-2`}>Prev.</th>
                  <th className={t.th}>Status</th>
                  <th className={t.th}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-8 text-center text-sm text-[#4A4A4A]">
                      No fields match this filter.
                    </td>
                  </tr>
                ) : (
                  pageRows.map((row) => {
                    const config = getRowConfig(pkg, row);
                    const temporal = classifyEdi837Temporal(row);
                    const preview = previewMappingValue(row, config);
                    const activeFormat = effectiveFormatId(row, config);
                    const statusStyle = STATUS_STYLE[config.status];
                    const modeLabel = transformModeLabel(row, config);
                    return (
                      <tr
                        key={row.id}
                        className={`${t.tableRow} align-middle ${temporal ? "cursor-pointer" : ""}`}
                        onClick={() => temporal && openEdit(row)}
                      >
                        <td className={`${t.tdMiddle} overflow-hidden max-w-0`}>
                          <code className="font-mono text-xs font-bold text-[#212121] block truncate">
                            {row.segment_id}-{row.element_id}
                          </code>
                          <div
                            className="text-[10px] text-[#0097AC] font-bold uppercase tracking-[0.08em] mt-0.5 truncate"
                            title={row.section_title}
                          >
                            {row.section_title}
                          </div>
                          <div
                            className="text-[10px] text-[#4A4A4A] mt-0.5 truncate"
                            title={row.element_name}
                          >
                            {row.element_name}
                          </div>
                        </td>
                        <td className={t.tdMiddle}>
                          <code className="font-mono text-sm text-[#006E74] break-all leading-snug block">
                            {row.target}
                          </code>
                        </td>
                        <td className={`${t.tdMiddle} overflow-hidden max-w-0`}>
                          <span className="text-xs font-bold text-[#212121] block">{modeLabel}</span>
                          {temporal && config.mode === "transform" && activeFormat && (
                            <div
                              className="text-[10px] text-[#4A4A4A] mt-0.5 leading-snug truncate"
                              title={formatLabel(activeFormat)}
                            >
                              {formatLabel(activeFormat)}
                            </div>
                          )}
                        </td>
                        <td className={`${t.tdMiddle} overflow-hidden max-w-0 px-2`}>
                          <code className={compactSampleClass} title={row.value || undefined}>
                            {row.value || "—"}
                          </code>
                        </td>
                        <td className={`${t.tdMiddle} overflow-hidden max-w-0 px-2`}>
                          <code
                            className={`${compactSampleClass} text-[#212121]`}
                            title={preview || undefined}
                          >
                            {preview || "—"}
                          </code>
                        </td>
                        <td className={t.tdMiddle}>
                          <span
                            className={`text-[10px] font-bold px-2 py-1 border whitespace-nowrap ${statusStyle.chip}`}
                          >
                            {statusStyle.label}
                          </span>
                        </td>
                        <td className={t.tdMiddle} onClick={(e) => e.stopPropagation()}>
                          <div className="flex flex-nowrap items-center justify-end gap-1">
                            {temporal ? (
                              <button
                                type="button"
                                onClick={() => openEdit(row)}
                                className="text-[11px] font-bold px-2 py-1 border border-[#E0E0E0] bg-white hover:border-[#0097AC]"
                              >
                                Edit
                              </button>
                            ) : (
                              <span className="text-[10px] text-[#4A4A4A]">—</span>
                            )}
                            {config.status !== "approved" && (
                              <button
                                type="button"
                                onClick={() => approveRow(row)}
                                className="text-[11px] font-bold px-2 py-1 bg-[#006E74] text-white hover:bg-[#0097AC]"
                              >
                                Approve
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 border-t border-[#E0E0E0] bg-[#F5F5F5]">
            <div className="flex items-center gap-2 text-xs text-[#4A4A4A]">
              <button
                type="button"
                disabled={safePage <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                className="font-bold px-2 py-1 border border-[#E0E0E0] bg-white disabled:opacity-40"
              >
                ←
              </button>
              <span className="font-mono">{safePage} / {totalPages}</span>
              <button
                type="button"
                disabled={safePage >= totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                className="font-bold px-2 py-1 border border-[#E0E0E0] bg-white disabled:opacity-40"
              >
                →
              </button>
            </div>
            <label className="text-xs text-[#4A4A4A]">
              Rows
              <select
                value={pageSize}
                onChange={(e) => setPageSize(Number(e.target.value) as PageSizeOption)}
                className="ml-2 text-[11px] border border-[#E0E0E0] bg-white px-2 py-1"
              >
                {PAGE_SIZE_OPTIONS.map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
            </label>
          </div>
        </section>

        <p className="text-xs text-[#4A4A4A] mt-6">
          Session {result.hl7_session_id} · {new Date(result.created_at).toLocaleString()}
        </p>
      </main>

      {selectedRow && classifyEdi837Temporal(selectedRow) && (
        <>
          <div className={t.drawerOverlay} onClick={() => setSelectedRowId(null)} aria-hidden="true" />
          <aside className={t.drawer} role="dialog" aria-modal="true" aria-label="Date/time mapping">
            <header className="px-5 py-4 border-b border-[#E0E0E0] bg-[#F5F5F5] shrink-0">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className={t.eyebrow + " mb-1"}>Date / time format</div>
                  <code className="font-mono text-sm font-bold text-[#212121] block">
                    {selectedRow.segment_id}-{selectedRow.element_id}
                  </code>
                  <div className="text-xs text-[#4A4A4A] mt-1">{selectedRow.element_name}</div>
                </div>
                <button
                  type="button"
                  onClick={() => setSelectedRowId(null)}
                  className="text-[#4A4A4A] hover:text-[#212121] font-bold text-xl leading-none"
                  aria-label="Close"
                >
                  ×
                </button>
              </div>
            </header>
            <div className="flex-1 overflow-y-auto px-5 py-4">
              <section className="mb-6">
                <div className={t.eyebrow + " mb-2"}>Example</div>
                <code className="text-sm bg-[#F5F5F5] px-2 py-1">{selectedRow.value || "—"}</code>
              </section>
              <section className="mb-6">
                <div className={t.eyebrow + " mb-2"}>Output mode</div>
                <div className="flex flex-col gap-2">
                  <label className="flex items-center gap-2 text-sm cursor-pointer">
                    <input
                      type="radio"
                      name="mode"
                      checked={drawerMode === "direct"}
                      onChange={() => setDrawerMode("direct")}
                    />
                    Direct — keep raw X12 value in JSON
                  </label>
                  <label className="flex items-center gap-2 text-sm cursor-pointer">
                    <input
                      type="radio"
                      name="mode"
                      checked={drawerMode === "transform"}
                      onChange={() => setDrawerMode("transform")}
                    />
                    Transform — apply a format below
                  </label>
                </div>
              </section>
              {drawerMode === "transform" && (
                <section className="mb-6">
                  <div className={t.eyebrow + " mb-2"}>Format</div>
                  <select
                    value={drawerFormat}
                    onChange={(e) => setDrawerFormat(e.target.value as Edi837DateTimeFormatId)}
                    className="w-full text-sm border border-[#E0E0E0] px-3 py-2"
                  >
                    {formatOptionsForKind(classifyEdi837Temporal(selectedRow)!).map((opt) => {
                      const suggested =
                        suggestDateTimeFormat(selectedRow, classifyEdi837Temporal(selectedRow)!);
                      return (
                        <option key={opt.id} value={opt.id}>
                          {opt.label}
                          {opt.id === suggested ? " (suggested)" : ""}
                        </option>
                      );
                    })}
                  </select>
                  <p className="text-xs text-[#4A4A4A] mt-2">
                    Preview:{" "}
                    <code className="bg-[#F5F5F5] px-1">
                      {previewMappingValue(selectedRow, {
                        mode: "transform",
                        formatId: drawerFormat,
                        status: "pending",
                      })}
                    </code>
                  </p>
                </section>
              )}
            </div>
            <footer className="px-5 py-4 border-t border-[#E0E0E0] flex gap-2 shrink-0">
              <button type="button" onClick={saveDrawer} className={t.btnPrimary + " flex-1"}>
                Save &amp; approve
              </button>
              <button type="button" onClick={() => setSelectedRowId(null)} className={t.btnOutline}>
                Cancel
              </button>
            </footer>
          </aside>
        </>
      )}
    </div>
  );
};

export default EDI837JsonMapping;
