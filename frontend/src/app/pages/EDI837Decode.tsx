import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronDown, ChevronRight } from "lucide-react";
import { sttmNav } from "../utils/sttmRoutes";
import { hl7Theme as t } from "./hl7Theme";
import type {
  EDI837DecodedElement,
  EDI837DecodedSegment,
  EDI837GuideSection,
  HL7ResultWithEdi,
} from "../end-points/ediApi";
import { segmentDisplayLabel } from "../utils/edi837ExportJson";

type SegmentRow = EDI837DecodedSegment;

const segmentKey = (seg: SegmentRow) => `${seg.sequence}-${seg.segment_id}`;

const ElementRows: React.FC<{ element: EDI837DecodedElement; depth?: number }> = ({
  element,
  depth = 0,
}) => (
  <>
    <tr className="border-b border-[#E0E0E0] last:border-b-0">
      <td
        className={`px-4 py-2 font-mono text-xs text-[#212121] whitespace-nowrap ${
          depth > 0 ? "pl-8" : ""
        }`}
      >
        {depth > 0 && (
          <span className="text-[#0097AC] mr-1.5 inline-block" aria-hidden>
            └
          </span>
        )}
        {element.element_id}
      </td>
      <td className={`px-4 py-2 text-[#212121] ${depth > 0 ? "text-xs" : ""}`}>
        {element.element_name}
      </td>
      <td className="px-4 py-2 font-mono text-[11px] text-[#006E74] whitespace-nowrap">
        {element.target || "—"}
      </td>
      <td className="px-4 py-2">
        {depth === 0 && element.children && element.children.length > 0 ? (
          <code className="text-[11px] bg-[#F5F5F5] px-1.5 py-0.5 break-all text-[#4A4A4A]">
            {element.value || "—"}
          </code>
        ) : (
          <code className="text-[11px] bg-[#F5F5F5] px-1.5 py-0.5 break-all text-[#212121]">
            {element.value || "—"}
          </code>
        )}
      </td>
      <td className="px-4 py-2 text-[#4A4A4A] text-xs">{element.meaning || "—"}</td>
    </tr>
    {element.children?.map((child) => (
      <ElementRows key={`${element.element_id}-${child.element_id}`} element={child} depth={depth + 1} />
    ))}
  </>
);

const chipClass = (active: boolean) =>
  `text-[11px] font-bold px-2.5 py-1 border shrink-0 ${
    active
      ? "bg-[#0097AC] text-white border-[#0097AC]"
      : "bg-white text-[#212121] border-[#E0E0E0] hover:border-[#0097AC]"
  }`;

const segmentHeaderTitle = (seg: SegmentRow): string => segmentDisplayLabel(seg);

const SegmentTable: React.FC<{
  segment: SegmentRow;
  expanded: boolean;
  onToggle: () => void;
  nested?: boolean;
}> = ({ segment, expanded, onToggle, nested }) => (
  <div
    className={`border border-[#E0E0E0] bg-white mb-2 last:mb-0 ${
      nested ? "ml-3 border-l-2 border-l-[#0097AC]" : ""
    }`}
  >
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
      <span className="text-sm text-[#4A4A4A]">{segmentHeaderTitle(segment)}</span>
      {segment.segment_id === "NM1" && segment.party_code && (
        <span className="text-[10px] font-mono text-[#4A4A4A]">*{segment.party_code}</span>
      )}
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
              <th className="px-4 py-2 font-bold w-[22%]">Name</th>
              <th className="px-4 py-2 font-bold w-[16%]">Target</th>
              <th className="px-4 py-2 font-bold w-[18%]">Value</th>
              <th className="px-4 py-2 font-bold w-[32%]">Meaning</th>
            </tr>
          </thead>
          <tbody>
            {segment.elements.map((el) => (
              <ElementRows key={el.element_id} element={el} />
            ))}
          </tbody>
        </table>
      </div>
    )}
  </div>
);

const CombinedGuideTable: React.FC<{ segments: SegmentRow[] }> = ({ segments }) => (
  <div className="overflow-x-auto border border-[#E0E0E0] bg-white">
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-[11px] uppercase tracking-[0.08em] text-[#4A4A4A] border-b border-[#E0E0E0]">
          <th className="px-4 py-2 font-bold w-[12%]">Element</th>
          <th className="px-4 py-2 font-bold w-[22%]">Name</th>
          <th className="px-4 py-2 font-bold w-[16%]">Target</th>
          <th className="px-4 py-2 font-bold w-[18%]">Value</th>
          <th className="px-4 py-2 font-bold w-[32%]">Meaning</th>
        </tr>
      </thead>
      <tbody>
        {segments.map((seg) => (
          <React.Fragment key={segmentKey(seg)}>
            <tr className="bg-[#E8F4F6] border-b border-[#E0E0E0]">
              <td colSpan={5} className="px-4 py-2 text-xs font-bold text-[#212121]">
                <span className="font-mono">{seg.segment_id}</span>
                <span className="text-[#4A4A4A] font-normal ml-2">{segmentHeaderTitle(seg)}</span>
                <span className="text-[#0097AC] ml-2">#{seg.sequence}</span>
              </td>
            </tr>
            {seg.elements.map((el) => (
              <ElementRows key={`${segmentKey(seg)}-${el.element_id}`} element={el} />
            ))}
          </React.Fragment>
        ))}
      </tbody>
    </table>
  </div>
);

const SectionBlock: React.FC<{
  section: EDI837GuideSection;
  sectionExpanded: boolean;
  onToggleSection: () => void;
  isSegmentExpanded: (seg: SegmentRow) => boolean;
  onToggleSegment: (seg: SegmentRow) => void;
}> = ({
  section,
  sectionExpanded,
  onToggleSection,
  isSegmentExpanded,
  onToggleSegment,
}) => (
  <div className="border border-[#E0E0E0] bg-[#FAFAFA] mb-4">
    <button
      type="button"
      onClick={onToggleSection}
      className="w-full px-4 py-3 flex items-center gap-2 text-left bg-white border-b border-[#E0E0E0] hover:bg-[#F5F5F5]"
      aria-expanded={sectionExpanded}
    >
      {sectionExpanded ? (
        <ChevronDown size={18} className="text-[#006E74] shrink-0" />
      ) : (
        <ChevronRight size={18} className="text-[#4A4A4A] shrink-0" />
      )}
      <span className="text-sm font-bold text-[#212121]">{section.title}</span>
      <span className="text-[10px] text-[#4A4A4A] ml-auto">
        {section.segments.length} segment{section.segments.length === 1 ? "" : "s"}
        {section.combined_table ? " · guide table" : ""}
      </span>
    </button>
    {sectionExpanded && (
      <div className="p-3">
        {section.combined_table ? (
          <CombinedGuideTable segments={section.segments} />
        ) : (
          section.segments.map((seg) => (
            <SegmentTable
              key={segmentKey(seg)}
              segment={seg}
              expanded={isSegmentExpanded(seg)}
              onToggle={() => onToggleSegment(seg)}
              nested
            />
          ))
        )}
      </div>
    )}
  </div>
);

function sectionsForMessage(msg: {
  sections?: EDI837GuideSection[];
  segments: SegmentRow[];
}): EDI837GuideSection[] {
  if (msg.sections && msg.sections.length > 0) {
    return msg.sections;
  }
  return [
    {
      section_id: "all",
      title: "All segments",
      segments: msg.segments,
    },
  ];
}

export const EDI837DecodeView: React.FC<{ result: HL7ResultWithEdi }> = ({ result }) => {
  const navigate = useNavigate();
  const decoded = result.edi_decoded;
  const files = decoded?.files ?? [];
  const [activeFile, setActiveFile] = useState(files[0]?.filename ?? "");
  const [sectionFilter, setSectionFilter] = useState<string>("all");
  const [collapsedKeys, setCollapsedKeys] = useState<Set<string>>(() => new Set());
  const [collapsedSections, setCollapsedSections] = useState<Set<string>>(() => new Set());

  const active = useMemo(
    () => files.find((f) => f.filename === activeFile) ?? files[0],
    [files, activeFile]
  );

  const messages = active?.messages ?? [];
  const meta = messages[0];

  const allSections = useMemo(() => {
    const list: EDI837GuideSection[] = [];
    for (const msg of messages) {
      list.push(...sectionsForMessage(msg));
    }
    return list;
  }, [messages]);

  const visibleSections = useMemo(() => {
    if (sectionFilter === "all") return allSections;
    return allSections.filter((s) => s.section_id === sectionFilter);
  }, [allSections, sectionFilter]);

  const visibleSegments = useMemo(
    () => visibleSections.flatMap((s) => s.segments),
    [visibleSections]
  );

  useEffect(() => {
    setSectionFilter("all");
    setCollapsedKeys(new Set());
    setCollapsedSections(new Set());
  }, [activeFile]);

  const isExpanded = useCallback(
    (seg: SegmentRow) => !collapsedKeys.has(segmentKey(seg)),
    [collapsedKeys]
  );

  const isSectionExpanded = useCallback(
    (section: EDI837GuideSection) => !collapsedSections.has(section.section_id),
    [collapsedSections]
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

  const toggleSection = useCallback((section: EDI837GuideSection) => {
    setCollapsedSections((prev) => {
      const next = new Set(prev);
      if (next.has(section.section_id)) next.delete(section.section_id);
      else next.add(section.section_id);
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
    setCollapsedSections((prev) => {
      const next = new Set(prev);
      for (const sec of visibleSections) {
        next.delete(sec.section_id);
      }
      return next;
    });
  }, [visibleSegments, visibleSections]);

  const collapseAll = useCallback(() => {
    setCollapsedKeys((prev) => {
      const next = new Set(prev);
      for (const seg of visibleSegments) {
        next.add(segmentKey(seg));
      }
      return next;
    });
    setCollapsedSections((prev) => {
      const next = new Set(prev);
      for (const sec of visibleSections) {
        next.add(sec.section_id);
      }
      return next;
    });
  }, [visibleSegments, visibleSections]);

  return (
    <div className={t.page}>
      <main className={t.container}>
        <div className="flex items-start justify-between mb-8 gap-4">
          <div>
            <div className={t.eyebrow}>X12 837 · HIPAA 005010</div>
            <h2 className={t.heading}>Claim segment decode</h2>
            <div className={t.accentRule} />
            <p className={t.subtext}>
              Grouped by companion-guide loops (NM1 with N3/N4, HI with claim CLM, etc.). File order
              is preserved within each section ({decoded?.guide_reference ?? "837 guide"}).
            </p>
          </div>
          <div className="flex flex-col gap-2 shrink-0">
            <button
              type="button"
              onClick={() => {
                navigate(sttmNav(`/hl7/${result.hl7_session_id}/json-mapping`));
              }}
              disabled={!decoded?.files.length}
              className={t.btnPrimary}
            >
              Map to JSON
            </button>
            <button type="button" onClick={() => navigate(sttmNav("/upload"))} className={t.btnOutline}>
              Upload more
            </button>
          </div>
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

        {allSections.length > 0 && (
          <section className={`${t.card} p-4 mb-4`}>
            <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
              <div className={t.eyebrow}>Filter by guide section</div>
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
                onClick={() => setSectionFilter("all")}
                className={chipClass(sectionFilter === "all")}
              >
                All ({allSections.length} sections)
              </button>
              {allSections.map((sec) => (
                <button
                  key={sec.section_id}
                  type="button"
                  onClick={() => setSectionFilter(sec.section_id)}
                  className={chipClass(sectionFilter === sec.section_id)}
                  title={sec.title}
                >
                  <span className="max-w-[220px] truncate inline-block align-bottom">
                    {sec.title}
                  </span>
                  {" "}({sec.segments.length})
                </button>
              ))}
            </div>
          </section>
        )}

        {messages.map((msg, mi) => {
          const sections = sectionsForMessage(msg).filter(
            (s) => sectionFilter === "all" || s.section_id === sectionFilter
          );
          if (sections.length === 0) return null;
          return (
            <section key={mi} className="mb-8">
              {messages.length > 1 && (
                <h3 className="text-sm font-bold text-[#212121] mb-3">
                  Transaction {mi + 1} ({msg.control_id})
                </h3>
              )}
              {sections.map((sec) => (
                <SectionBlock
                  key={`${mi}-${sec.section_id}`}
                  section={sec}
                  sectionExpanded={isSectionExpanded(sec)}
                  onToggleSection={() => toggleSection(sec)}
                  isSegmentExpanded={isExpanded}
                  onToggleSegment={toggleSegment}
                />
              ))}
            </section>
          );
        })}

        <p className="text-xs text-[#4A4A4A] mt-6">
          Session {result.hl7_session_id} · {new Date(result.created_at).toLocaleString()}
        </p>
      </main>
    </div>
  );
};

export default EDI837DecodeView;
