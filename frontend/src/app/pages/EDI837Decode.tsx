import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronDown, ChevronRight } from "lucide-react";
import { sttmNav } from "../utils/sttmRoutes";
import { hl7Theme as t } from "./hl7Theme";
import type {
  EDI837DecodedSegment,
  EDI837DecodePayload,
  HL7ResultWithEdi,
} from "../end-points/ediApi";

type SegmentRow = EDI837DecodedSegment;

const segmentKey = (seg: SegmentRow) => `${seg.sequence}-${seg.segment_id}`;

const chipClass = (active: boolean) =>
  `text-[11px] font-bold px-2.5 py-1 border shrink-0 ${
    active
      ? "bg-[#0097AC] text-white border-[#0097AC]"
      : "bg-white text-[#212121] border-[#E0E0E0] hover:border-[#0097AC]"
  }`;

const SegmentTable: React.FC<{
  segment: SegmentRow;
  expanded: boolean;
  onToggle: () => void;
}> = ({ segment, expanded, onToggle }) => (
  <div className="border border-[#E0E0E0] bg-white mb-2 last:mb-0">
    <button
      type="button"
      onClick={onToggle}
      className="w-full px-4 py-3 bg-[#F5F5F5] border-b border-[#E0E0E0] flex flex-wrap items-center gap-2 text-left hover:bg-[#ECECEC] transition-colors"
      aria-expanded={expanded}
    >
      {expanded ? (
        <ChevronDown size={16} className="text-[#0097AC] shrink-0" aria-hidden />
      ) : (
        <ChevronRight size={16} className="text-[#4A4A4A] shrink-0" aria-hidden />
      )}
      <span className="font-mono text-sm font-bold text-[#212121]">{segment.segment_id}</span>
      <span className="text-sm text-[#4A4A4A]">{segment.segment_name}</span>
      <span className="text-[10px] uppercase tracking-wider text-[#0097AC] font-bold">
        #{segment.sequence}
      </span>
      <span className="text-[10px] text-[#4A4A4A] ml-auto">
        {segment.elements.length} element{segment.elements.length === 1 ? "" : "s"}
      </span>
    </button>
    {expanded && (
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-[11px] uppercase tracking-[0.08em] text-[#4A4A4A] border-b border-[#E0E0E0]">
              <th className="px-4 py-2 font-bold w-[12%]">Element</th>
              <th className="px-4 py-2 font-bold w-[28%]">Name</th>
              <th className="px-4 py-2 font-bold w-[22%]">Value</th>
              <th className="px-4 py-2 font-bold w-[38%]">Meaning</th>
            </tr>
          </thead>
          <tbody>
            {segment.elements.map((el) => (
              <tr key={el.element_id} className="border-b border-[#E0E0E0] last:border-b-0">
                <td className="px-4 py-2 font-mono text-xs text-[#212121] whitespace-nowrap">
                  {el.element_id}
                </td>
                <td className="px-4 py-2 text-[#212121]">{el.element_name}</td>
                <td className="px-4 py-2">
                  <code className="text-[11px] bg-[#F5F5F5] px-1.5 py-0.5 break-all text-[#212121]">
                    {el.value || "—"}
                  </code>
                </td>
                <td className="px-4 py-2 text-[#4A4A4A] text-xs">{el.meaning || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )}
  </div>
);

/** Segment IDs in order of first appearance (file order). */
function segmentFilterOptions(segments: SegmentRow[]): { id: string; count: number }[] {
  const order: string[] = [];
  const counts = new Map<string, number>();
  for (const seg of segments) {
    counts.set(seg.segment_id, (counts.get(seg.segment_id) ?? 0) + 1);
    if (!order.includes(seg.segment_id)) {
      order.push(seg.segment_id);
    }
  }
  return order.map((id) => ({ id, count: counts.get(id) ?? 0 }));
}

export const EDI837DecodeView: React.FC<{ result: HL7ResultWithEdi }> = ({ result }) => {
  const navigate = useNavigate();
  const decoded = result.edi_decoded;
  const files = decoded?.files ?? [];
  const [activeFile, setActiveFile] = useState(files[0]?.filename ?? "");
  const [segmentFilter, setSegmentFilter] = useState<string>("all");
  /** Segments not in this set are expanded (default: all expanded). */
  const [collapsedKeys, setCollapsedKeys] = useState<Set<string>>(() => new Set());

  const active = useMemo(
    () => files.find((f) => f.filename === activeFile) ?? files[0],
    [files, activeFile]
  );

  const messages = active?.messages ?? [];
  const meta = messages[0];

  const allSegments = useMemo(() => {
    const list: SegmentRow[] = [];
    for (const msg of messages) {
      list.push(...msg.segments);
    }
    return list;
  }, [messages]);

  const filterOptions = useMemo(() => segmentFilterOptions(allSegments), [allSegments]);

  const visibleSegments = useMemo(() => {
    if (segmentFilter === "all") return allSegments;
    return allSegments.filter((s) => s.segment_id === segmentFilter);
  }, [allSegments, segmentFilter]);

  useEffect(() => {
    setSegmentFilter("all");
    setCollapsedKeys(new Set());
  }, [activeFile]);

  const isExpanded = useCallback(
    (seg: SegmentRow) => !collapsedKeys.has(segmentKey(seg)),
    [collapsedKeys]
  );

  const toggleSegment = useCallback((seg: SegmentRow) => {
    const key = segmentKey(seg);
    setCollapsedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const expandAllVisible = useCallback(() => {
    setCollapsedKeys((prev) => {
      const next = new Set(prev);
      for (const seg of visibleSegments) {
        next.delete(segmentKey(seg));
      }
      return next;
    });
  }, [visibleSegments]);

  const collapseAll = useCallback(() => {
    setCollapsedKeys((prev) => {
      const next = new Set(prev);
      for (const seg of visibleSegments) {
        next.add(segmentKey(seg));
      }
      return next;
    });
  }, [visibleSegments]);

  return (
    <div className={t.page}>
      <main className={t.container}>
        <div className="flex items-start justify-between mb-8 gap-4">
          <div>
            <div className={t.eyebrow}>X12 837 · HIPAA 005010</div>
            <h2 className={t.heading}>Claim segment decode</h2>
            <div className={t.accentRule} />
            <p className={t.subtext}>
              Segments stay in file order. Filter by segment type; expand a row to see elements and
              meanings ({decoded?.guide_reference ?? "837 guide"}).
            </p>
          </div>
          <button type="button" onClick={() => navigate(sttmNav("/upload"))} className={t.btnOutline}>
            Upload more
          </button>
        </div>

        {files.length > 1 && (
          <div className="mb-4 flex flex-wrap gap-0 border border-[#E0E0E0] bg-white overflow-x-auto">
            {files.map((file) => {
              const selected = file.filename === active?.filename;
              const segCount = file.messages.reduce((n, m) => n + m.segments.length, 0);
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
                  <span className="block truncate max-w-[200px]">{file.filename}</span>
                  <span
                    className={`text-[10px] font-normal ${selected ? "text-white/80" : "text-[#4A4A4A]"}`}
                  >
                    {segCount} segments
                  </span>
                </button>
              );
            })}
          </div>
        )}

        {meta && (
          <p className="text-xs text-[#4A4A4A] mb-4">
            Guide {meta.implementation_guide || "—"} · Control {meta.control_id || "—"} · Version{" "}
            {meta.version || "—"}
          </p>
        )}

        {allSegments.length > 0 && (
          <section className={`${t.card} p-4 mb-4`}>
            <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
              <div className={t.eyebrow}>Filter by segment</div>
              <div className="flex gap-2 text-xs">
                <button type="button" onClick={expandAllVisible} className={t.link}>
                  Expand all
                </button>
                <span className="text-[#E0E0E0]">|</span>
                <button type="button" onClick={collapseAll} className={t.link}>
                  Collapse all
                </button>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => setSegmentFilter("all")}
                className={chipClass(segmentFilter === "all")}
              >
                All ({allSegments.length})
              </button>
              {filterOptions.map(({ id, count }) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setSegmentFilter(id)}
                  className={chipClass(segmentFilter === id)}
                >
                  {id} ({count})
                </button>
              ))}
            </div>
            {segmentFilter !== "all" && (
              <p className="text-xs text-[#4A4A4A] mt-3">
                Showing {visibleSegments.length} {segmentFilter} segment
                {visibleSegments.length === 1 ? "" : "s"} in file order.
              </p>
            )}
          </section>
        )}

        {messages.length > 1 ? (
          messages.map((msg, mi) => {
            const msgSegments = msg.segments.filter(
              (s) => segmentFilter === "all" || s.segment_id === segmentFilter
            );
            if (msgSegments.length === 0) return null;
            return (
              <section key={mi} className="mb-8">
                <h3 className="text-sm font-bold text-[#212121] mb-3">
                  Transaction {mi + 1} ({msg.control_id})
                </h3>
                {msgSegments.map((seg) => (
                  <SegmentTable
                    key={segmentKey(seg)}
                    segment={seg}
                    expanded={isExpanded(seg)}
                    onToggle={() => toggleSegment(seg)}
                  />
                ))}
              </section>
            );
          })
        ) : (
          <section className="mb-8">
            {visibleSegments.length === 0 ? (
              <p className="text-sm text-[#4A4A4A]">No segments match this filter.</p>
            ) : (
              visibleSegments.map((seg) => (
                <SegmentTable
                  key={segmentKey(seg)}
                  segment={seg}
                  expanded={isExpanded(seg)}
                  onToggle={() => toggleSegment(seg)}
                />
              ))
            )}
          </section>
        )}

        <p className="text-xs text-[#4A4A4A] mt-6">
          Session {result.hl7_session_id} · {new Date(result.created_at).toLocaleString()}
        </p>
      </main>
    </div>
  );
};

export default EDI837DecodeView;
