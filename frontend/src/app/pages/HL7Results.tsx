import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { sttmNav } from "../utils/sttmRoutes";
import {
  getHL7Fhir,
  getHL7Session,
  type HL7FieldProfile,
  type HL7FileResult,
  type HL7Inference,
  type HL7Result,
  type HL7Routing,
  type HL7SegmentProfile,
} from "../end-points/hl7Api";
import { hl7Theme as t } from "./hl7Theme";

const ROUTING_ORDER: HL7Routing[] = [
  "AUTO_MAP",
  "AUTO_MAP_OPTIONAL",
  "REVIEW",
  "HUMAN_REQUIRED",
];

const ROUTING_STYLE: Record<HL7Routing, { bar: string; chip: string; label: string }> = {
  AUTO_MAP: { bar: "bg-[#006E74]", chip: "border-[#006E74] text-[#006E74]", label: "Auto map" },
  AUTO_MAP_OPTIONAL: {
    bar: "bg-[#0097AC]",
    chip: "border-[#0097AC] text-[#0097AC]",
    label: "Auto map (optional)",
  },
  REVIEW: { bar: "bg-[#4A4A4A]", chip: "border-[#4A4A4A] text-[#4A4A4A]", label: "Review" },
  HUMAN_REQUIRED: {
    bar: "bg-[#EF9A9A]",
    chip: "border-[#EF9A9A] text-[#212121]",
    label: "Human required",
  },
};

type SegmentFilter = "all" | "standard" | "site_defined" | string;

const chipClass = (active: boolean) =>
  `text-[11px] font-bold px-2.5 py-1 border shrink-0 ${
    active
      ? "bg-[#0097AC] text-white border-[#0097AC]"
      : "bg-white text-[#212121] border-[#E0E0E0] hover:border-[#0097AC]"
  }`;

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

const ScoreBar: React.FC<{ value: number; tone: string; caption?: string }> = ({
  value,
  tone,
  caption,
}) => (
  <div className="flex items-center gap-2 w-full max-w-[140px]">
    <div className="flex-1 h-2 bg-[#E0E0E0] overflow-hidden min-w-0">
      <div className={`h-full ${tone}`} style={{ width: `${Math.round(value * 100)}%` }} />
    </div>
    <span className="font-mono text-xs text-[#212121] w-8 text-right shrink-0">{value.toFixed(2)}</span>
    {caption && <span className="text-[11px] text-[#4A4A4A] whitespace-nowrap shrink-0">{caption}</span>}
  </div>
);

const FieldTableColGroup = () => (
  <colgroup>
    <col className="w-[22%]" />
    <col className="w-[18%]" />
    <col className="w-[60%]" />
  </colgroup>
);

const InferenceTableColGroup = () => (
  <colgroup>
    <col className="w-[10%]" />
    <col className="w-[24%]" />
    <col className="w-[18%]" />
    <col className="w-[14%]" />
    <col className="w-[14%]" />
    <col className="w-[12%]" />
    <col className="w-[8%]" />
  </colgroup>
);

const InferenceRow: React.FC<{ inf: HL7Inference }> = ({ inf }) => {
  const [open, setOpen] = useState(false);
  const style = ROUTING_STYLE[inf.routing];
  const semanticTone =
    inf.semantic_confidence >= 0.85
      ? "bg-[#006E74]"
      : inf.semantic_confidence >= 0.5
        ? "bg-[#0097AC]"
        : "bg-[#EF9A9A]";
  const stabilityTone = inf.stability >= 1 ? "bg-[#006E74]" : "bg-[#0097AC]";

  return (
    <>
      <tr className={t.tableRow}>
        <td className={`${t.td} font-mono text-xs text-[#212121] whitespace-nowrap`}>{inf.path}</td>
        <td className={t.td}>
          <div className="font-medium text-[#212121]">{inf.inferred_meaning}</div>
          <div className="text-xs text-[#4A4A4A] mt-0.5">
            {inf.label ? `label ${inf.label}` : "no inline label"} · {inf.datatype}
          </div>
        </td>
        <td className={t.td}>
          <div className="flex flex-wrap gap-1">
            {inf.observed_values.slice(0, 3).map((v) => (
              <code key={v} className="text-[11px] bg-[#F5F5F5] px-1.5 py-0.5 text-[#212121] break-all">
                {v}
              </code>
            ))}
          </div>
        </td>
        <td className={t.tdMiddle}>
          <ScoreBar value={inf.semantic_confidence} tone={semanticTone} />
        </td>
        <td className={t.tdMiddle}>
          <ScoreBar
            value={inf.stability}
            tone={stabilityTone}
            caption={`${inf.present_in}/${inf.total_occurrences}`}
          />
        </td>
        <td className={t.tdMiddle}>
          <span className={`text-[11px] font-bold px-2 py-1 border ${style.chip} whitespace-nowrap`}>
            {style.label}
          </span>
        </td>
        <td className={`${t.tdMiddle} text-right`}>
          <button type="button" onClick={() => setOpen((v) => !v)} className={`text-xs ${t.link}`}>
            {open ? "Hide" : "Why?"}
          </button>
        </td>
      </tr>
      {open && (
        <tr className="bg-[#F5F5F5] border-t border-[#E0E0E0]">
          <td colSpan={7} className="px-4 py-4">
            <div className={t.eyebrow + " mb-2"}>Reasoning</div>
            <ul className="space-y-1.5 mb-3">
              {inf.reasoning.map((reason) => {
                const isWarning = reason.startsWith("WARNING") || reason.startsWith("CARDINALITY");
                return (
                  <li
                    key={reason}
                    className={`text-sm pl-4 relative ${isWarning ? "text-[#212121] font-bold" : "text-[#4A4A4A]"}`}
                  >
                    <span className="absolute left-0 top-2 w-1.5 h-1.5 bg-[#0097AC]" />
                    {reason}
                  </li>
                );
              })}
            </ul>
            <div className="text-xs text-[#4A4A4A]">
              FHIR target:{" "}
              <code className="bg-white border border-[#E0E0E0] px-1.5 py-0.5">{inf.fhir_target}</code>
            </div>
          </td>
        </tr>
      )}
    </>
  );
};

const FieldRow: React.FC<{ field: HL7FieldProfile }> = ({ field }) => {
  const rate = field.total ? Math.round((field.populated_in / field.total) * 100) : 0;
  return (
    <tr className={t.tableRow}>
      <td className={`${t.td} font-mono text-xs text-[#212121] whitespace-nowrap`}>{field.path}</td>
      <td className={`${t.tdMiddle} text-xs text-[#4A4A4A] whitespace-nowrap`}>
        {field.populated_in}/{field.total} ({rate}%)
      </td>
      <td className={t.td}>
        <div className="flex flex-wrap gap-1">
          {field.samples.map((sample) => (
            <code key={sample} className="text-[11px] bg-[#F5F5F5] px-1.5 py-0.5 text-[#212121]">
              {sample}
            </code>
          ))}
        </div>
      </td>
    </tr>
  );
};

const SegmentBlock: React.FC<{ seg: HL7SegmentProfile; expanded: boolean; onToggle: () => void }> = ({
  seg,
  expanded,
  onToggle,
}) => (
  <div className="border border-[#E0E0E0] bg-white mb-2 last:mb-0">
    <button
      type="button"
      onClick={onToggle}
      className="w-full px-4 py-2.5 flex items-center justify-between gap-3 text-left bg-[#F5F5F5] hover:bg-white border-b border-[#E0E0E0] transition-colors"
    >
      <div className="flex items-center gap-2 min-w-0 flex-wrap">
        <code className="font-bold text-sm text-[#212121]">{seg.name}</code>
        {seg.is_z ? (
          <span className={t.chipZ}>Site-defined</span>
        ) : (
          <span className={t.chipStd}>Standard</span>
        )}
        <span className="text-xs text-[#4A4A4A]">
          {seg.fields.length} field(s)
        </span>
      </div>
      <span className={`text-xs ${t.link} shrink-0`}>{expanded ? "Collapse" : "Expand"}</span>
    </button>

    {expanded && (
      <div className="overflow-x-auto">
        {seg.fields.length > 0 ? (
          <table className={t.table}>
            <FieldTableColGroup />
            <thead>
              <tr className={t.tableHead}>
                <th className={t.th}>Field path</th>
                <th className={t.th}>Population</th>
                <th className={t.th}>Sample values</th>
              </tr>
            </thead>
            <tbody>
              {seg.fields.map((field) => (
                <FieldRow key={field.path} field={field} />
              ))}
            </tbody>
          </table>
        ) : (
          <div className="px-4 py-3 text-sm text-[#4A4A4A]">No populated fields recorded.</div>
        )}
      </div>
    )}
  </div>
);

const InferenceBlock: React.FC<{ segment: string; rows: HL7Inference[] }> = ({ segment, rows }) => {
  const [open, setOpen] = useState(false);
  return (
    <div className="border border-[#E0E0E0] bg-white mb-2 last:mb-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full px-4 py-2.5 flex items-center justify-between gap-3 text-left bg-[#F5F5F5] hover:bg-white border-b border-[#E0E0E0]"
      >
        <div className="flex items-center gap-2">
          <code className="font-bold text-sm text-[#212121]">{segment}</code>
          <span className={t.chipZ}>Site-defined</span>
          <span className="text-xs text-[#4A4A4A]">{rows.length} field(s)</span>
        </div>
        <span className={`text-xs ${t.link}`}>{open ? "Collapse" : "Expand"}</span>
      </button>
      {open && (
        <div className={t.scrollPanel}>
          <table className={t.table}>
            <InferenceTableColGroup />
            <thead className="sticky top-0 z-10">
              <tr className={t.tableHead}>
                <th className={t.th}>Path</th>
                <th className={t.th}>Inferred meaning</th>
                <th className={t.th}>Values</th>
                <th className={t.th}>Semantic</th>
                <th className={t.th}>Stability</th>
                <th className={t.th}>Routing</th>
                <th className={t.th} />
              </tr>
            </thead>
            <tbody>
              {rows.map((inf) => (
                <InferenceRow key={inf.path} inf={inf} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

const fileSegments = (file: HL7FileResult, corpusSegments: HL7SegmentProfile[]): HL7SegmentProfile[] => {
  if (file.profile?.segments?.length) return file.profile.segments;
  const names = file.segments ?? [];
  return names.map((name) => {
    const fromCorpus = corpusSegments.find((s) => s.name === name);
    if (fromCorpus) return fromCorpus;
    return {
      name,
      is_z: /^Z/.test(name),
      occurrences: 1,
      messages_present: 1,
      max_fields: 0,
      fields: [],
    };
  });
};

const filterSegments = (segments: HL7SegmentProfile[], filter: SegmentFilter): HL7SegmentProfile[] => {
  if (filter === "all") return segments;
  if (filter === "standard") return segments.filter((s) => !s.is_z);
  if (filter === "site_defined") return segments.filter((s) => s.is_z);
  return segments.filter((s) => s.name === filter);
};

interface FileProfilePanelProps {
  file: HL7FileResult;
  corpusSegments: HL7SegmentProfile[];
  segmentFilter: SegmentFilter;
  onSegmentFilter: (f: SegmentFilter) => void;
  expandedSegment: string | null;
  onToggleSegment: (name: string) => void;
  fhirOpen: boolean;
  fhirDoc: Record<string, unknown> | null;
  onToggleFhir: () => void;
}

const FileProfilePanel: React.FC<FileProfilePanelProps> = ({
  file,
  corpusSegments,
  segmentFilter,
  onSegmentFilter,
  expandedSegment,
  onToggleSegment,
  fhirOpen,
  fhirDoc,
  onToggleFhir,
}) => {
  const segments = fileSegments(file, corpusSegments);
  const inference = file.inference ?? {};
  const zCount = segments.filter((s) => s.is_z).length;
  const stdCount = segments.length - zCount;
  const visibleSegments = filterSegments(segments, segmentFilter);
  const segmentNames = useMemo(
    () => [...new Set(segments.map((s) => s.name))].sort(),
    [segments]
  );

  if (file.status === "failed") {
    return (
      <div className={t.note}>
        <span className="font-bold text-[#212121]">{file.filename}</span> — Parse failed: {file.error}
      </div>
    );
  }

  return (
    <>
      <div className={`${t.cardHeader} flex items-start justify-between gap-4 flex-wrap`}>
        <div className="min-w-0">
          <div className="font-bold text-[#212121] truncate">{file.filename}</div>
          <div className="text-xs text-[#4A4A4A] mt-1">
            {file.message_type} · v{file.version} · {segments.length} segments · {stdCount} standard ·{" "}
            {zCount} site-defined
          </div>
        </div>
        <button type="button" onClick={onToggleFhir} className={`text-xs ${t.link} shrink-0`}>
          {fhirOpen ? "Hide FHIR" : "View FHIR"}
        </button>
      </div>

      <div className={t.chipRow}>
        <button type="button" onClick={() => onSegmentFilter("all")} className={chipClass(segmentFilter === "all")}>
          All <span className="ml-1 opacity-80">{segments.length}</span>
        </button>
        <button
          type="button"
          onClick={() => onSegmentFilter("standard")}
          className={chipClass(segmentFilter === "standard")}
        >
          Standard <span className="ml-1 opacity-80">{stdCount}</span>
        </button>
        {zCount > 0 && (
          <button
            type="button"
            onClick={() => onSegmentFilter("site_defined")}
            className={chipClass(segmentFilter === "site_defined")}
          >
            Site-defined <span className="ml-1 opacity-80">{zCount}</span>
          </button>
        )}
        {segmentNames.map((name) => (
          <button
            key={name}
            type="button"
            onClick={() => onSegmentFilter(name)}
            className={`${chipClass(segmentFilter === name)} font-mono`}
          >
            {name}
          </button>
        ))}
      </div>

      <div className={t.cardBody}>
        <p className="text-sm text-[#4A4A4A] mb-3">
          {visibleSegments.length} segment(s) in view · expand one row to inspect fields
        </p>

        <div className={t.scrollPanel}>
          {visibleSegments.length === 0 ? (
            <div className="p-6 text-sm text-[#4A4A4A] text-center">No segments match this filter.</div>
          ) : (
            visibleSegments.map((seg) => (
              <SegmentBlock
                key={seg.name}
                seg={seg}
                expanded={expandedSegment === seg.name}
                onToggle={() => onToggleSegment(seg.name)}
              />
            ))
          )}
        </div>

        {Object.keys(inference).length > 0 && (
          <div className="mt-6">
            <div className={t.eyebrow + " mb-2"}>Site-defined inference</div>
            <div className={t.scrollPanel}>
              {Object.entries(inference).map(([segment, rows]) => (
                <InferenceBlock key={segment} segment={segment} rows={rows} />
              ))}
            </div>
          </div>
        )}

        {fhirOpen && (
          <pre className={`${t.codeBlock} mt-4 max-h-[min(360px,40vh)]`}>
            {fhirDoc ? JSON.stringify(fhirDoc, null, 2) : "Loading..."}
          </pre>
        )}
      </div>
    </>
  );
};

const HL7Results: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const params = useParams<{ hl7SessionId?: string }>();

  const stateResult = (location.state as { result?: HL7Result } | null)?.result ?? null;
  const [result, setResult] = useState<HL7Result | null>(stateResult);
  const [loading, setLoading] = useState<boolean>(!stateResult);
  const [error, setError] = useState<string | null>(null);
  const [fhirName, setFhirName] = useState<string | null>(null);
  const [fhirDoc, setFhirDoc] = useState<Record<string, unknown> | null>(null);
  const [activeFile, setActiveFile] = useState<string>("");
  const [segmentFilter, setSegmentFilter] = useState<SegmentFilter>("all");
  const [expandedSegment, setExpandedSegment] = useState<string | null>(null);

  const sessionId = result?.hl7_session_id ?? params.hl7SessionId;

  useEffect(() => {
    if (result || !params.hl7SessionId) return;
    let cancelled = false;
    setLoading(true);
    getHL7Session(params.hl7SessionId)
      .then((data) => !cancelled && setResult(data))
      .catch((err) =>
        !cancelled && setError(err instanceof Error ? err.message : "Failed to load HL7 session")
      )
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [params.hl7SessionId, result]);

  useEffect(() => {
    if (!result?.files.length) return;
    if (!activeFile || !result.files.some((f) => f.filename === activeFile)) {
      setActiveFile(result.files[0].filename);
    }
  }, [result, activeFile]);

  useEffect(() => {
    setSegmentFilter("all");
    setExpandedSegment(null);
  }, [activeFile]);

  const openFhir = useCallback(
    async (name: string) => {
      if (!sessionId) return;
      if (fhirName === name) {
        setFhirName(null);
        setFhirDoc(null);
        return;
      }
      setFhirName(name);
      setFhirDoc(null);
      try {
        setFhirDoc(await getHL7Fhir(sessionId, name));
      } catch {
        setFhirDoc({ error: "Could not load FHIR document" });
      }
    },
    [sessionId, fhirName]
  );

  const toggleSegment = useCallback((name: string) => {
    setExpandedSegment((cur) => (cur === name ? null : name));
  }, []);

  const totalZFields = useMemo(
    () => (result ? Object.values(result.routing_summary).reduce((a, b) => a + b, 0) : 0),
    [result]
  );
  const resolvedWithoutHuman = useMemo(
    () =>
      result
        ? (result.routing_summary.AUTO_MAP ?? 0) + (result.routing_summary.AUTO_MAP_OPTIONAL ?? 0)
        : 0,
    [result]
  );
  const totalSegments = useMemo(() => result?.profile.segments.length ?? 0, [result]);

  const activeFileObj = result?.files.find((f) => f.filename === activeFile);

  if (loading) {
    return (
      <div className={`${t.page} flex items-center justify-center`}>
        <div className="font-bold text-[#4A4A4A]">Loading HL7 results…</div>
      </div>
    );
  }
  if (error || !result) {
    return (
      <div className={`${t.page} flex items-center justify-center`}>
        <div className="text-[#212121] font-bold">{error ?? "No HL7 results available."}</div>
      </div>
    );
  }

  const { summary, profile, files, routing_summary } = result;

  return (
    <div className={t.page}>
      <main className={t.container}>
        <div className="flex items-start justify-between mb-8 gap-4">
          <div>
            <div className={t.eyebrow}>HL7 v2 ingestion</div>
            <h2 className={t.heading}>Message profile by file</h2>
            <div className={t.accentRule} />
            <p className={t.subtext}>
              One file at a time. Filter by segment, expand rows on demand — scrolling stays inside
              each panel.
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button type="button" onClick={() => navigate(sttmNav("/upload"))} className={t.btnOutline}>
              Upload more
            </button>
            <button
              type="button"
              onClick={() => navigate(sttmNav(`/hl7/${result.hl7_session_id}/review`))}
              className={t.btnPrimary}
            >
              Continue to Z-segment review
              {result.mapping_summary
                ? ` (${result.mapping_summary.custom ?? result.mapping_summary.total})`
                : ""}
            </button>
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-8">
          <StatCard
            value={`${summary.messages_parsed}/${summary.files_received}`}
            label="Messages parsed"
            accent={summary.messages_failed ? "text-[#0097AC]" : "text-[#006E74]"}
          />
          <StatCard value={Object.keys(summary.message_types).length} label="Message types" />
          <StatCard value={totalSegments} label="Distinct segments" />
          <StatCard value={summary.z_segment_names.length} label="Site-defined segments" />
          <StatCard value={summary.fhir_documents} label="FHIR documents" accent="text-[#006E74]" />
        </div>

        <section className={`${t.card} mb-8 p-6`}>
          <div className="flex items-baseline justify-between mb-4">
            <h3 className="font-bold text-[#212121]">Corpus Z-segment routing</h3>
            <span className="text-sm text-[#4A4A4A]">
              {resolvedWithoutHuman}/{totalZFields} resolved without human input
            </span>
          </div>
          <div className="flex h-3 overflow-hidden mb-4">
            {ROUTING_ORDER.map((r) =>
              routing_summary[r] ? (
                <div
                  key={r}
                  className={ROUTING_STYLE[r].bar}
                  style={{ width: `${(routing_summary[r] / Math.max(totalZFields, 1)) * 100}%` }}
                  title={`${ROUTING_STYLE[r].label}: ${routing_summary[r]}`}
                />
              ) : null
            )}
          </div>
          <div className="flex flex-wrap gap-4">
            {ROUTING_ORDER.map((r) => (
              <div key={r} className="flex items-center gap-2 text-sm">
                <span className={`w-3 h-3 ${ROUTING_STYLE[r].bar}`} />
                <span className="text-[#4A4A4A]">{ROUTING_STYLE[r].label}</span>
                <span className="font-bold text-[#212121]">{routing_summary[r] ?? 0}</span>
              </div>
            ))}
          </div>
        </section>

        {files.length > 1 && (
          <div className="mb-4 flex flex-wrap gap-0 border border-[#E0E0E0] bg-white overflow-x-auto">
            {files.map((file) => {
              const active = file.filename === activeFile;
              const segCount = file.segment_count ?? file.segments?.length ?? 0;
              return (
                <button
                  key={file.filename}
                  type="button"
                  onClick={() => setActiveFile(file.filename)}
                  className={`text-xs font-bold px-4 py-3 border-r border-[#E0E0E0] last:border-r-0 transition shrink-0 ${
                    active
                      ? "bg-[#0097AC] text-white border-b-[3px] border-b-[#006E74]"
                      : "bg-white text-[#212121] hover:bg-[#F5F5F5] border-b-[3px] border-b-transparent"
                  }`}
                >
                  <span className="block truncate max-w-[200px]">{file.filename}</span>
                  <span className={`text-[10px] font-normal ${active ? "text-white/80" : "text-[#4A4A4A]"}`}>
                    {file.status === "parsed" ? `${segCount} segments` : "failed"}
                  </span>
                </button>
              );
            })}
          </div>
        )}

        {activeFileObj && (
          <section className={`${t.card} overflow-hidden`}>
            <FileProfilePanel
              file={activeFileObj}
              corpusSegments={profile.segments}
              segmentFilter={segmentFilter}
              onSegmentFilter={setSegmentFilter}
              expandedSegment={expandedSegment}
              onToggleSegment={toggleSegment}
              fhirOpen={fhirName === activeFile.replace(/\.[^.]+$/, "")}
              fhirDoc={fhirName === activeFile.replace(/\.[^.]+$/, "") ? fhirDoc : null}
              onToggleFhir={() => void openFhir(activeFile.replace(/\.[^.]+$/, ""))}
            />
          </section>
        )}

        <p className="text-xs text-[#4A4A4A] mt-6">
          Session {result.hl7_session_id} · {new Date(result.created_at).toLocaleString()}
        </p>
      </main>
    </div>
  );
};

export default HL7Results;
