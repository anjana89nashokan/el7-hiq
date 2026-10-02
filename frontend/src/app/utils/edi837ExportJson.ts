import type {
  EDI837DecodedElement,
  EDI837DecodePayload,
  EDI837DecodedSegment,
  EDI837GuideSection,
} from "../end-points/ediApi";
import { mappingRowId } from "./edi837MappingRows";
import { applyEdi837Transform, type Edi837TransformMap } from "./edi837Transforms";

export type { Edi837TransformMap };

function targetKey(el: EDI837DecodedElement): string {
  return el.target || el.element_id.replace(/-/g, "_").toLowerCase();
}

interface ElementExportCtx {
  filename: string;
  control_id: string;
  sequence: number;
  segment_id: string;
  transforms?: Edi837TransformMap;
}

function elementValue(el: EDI837DecodedElement, ctx: ElementExportCtx): unknown {
  if (el.children?.length) {
    const nested: Record<string, unknown> = {};
    for (const child of el.children) {
      nested[targetKey(child)] = elementValue(child, ctx);
    }
    if (el.value?.trim()) {
      nested._raw = el.value;
    }
    return nested;
  }
  const rowId = mappingRowId({
    filename: ctx.filename,
    control_id: ctx.control_id,
    sequence: ctx.sequence,
    segment_id: ctx.segment_id,
    element_id: el.element_id,
  });
  const transformId = ctx.transforms?.[rowId] ?? "passthrough";
  return applyEdi837Transform(el.value ?? "", transformId);
}

function segmentFields(
  segment: EDI837DecodedSegment,
  ctx: Omit<ElementExportCtx, "sequence" | "segment_id">
): Record<string, unknown> {
  const fields: Record<string, unknown> = {};
  const counts = new Map<string, number>();
  const elCtx: ElementExportCtx = {
    ...ctx,
    sequence: segment.sequence,
    segment_id: segment.segment_id,
  };
  for (const el of segment.elements) {
    const base = targetKey(el);
    const n = counts.get(base) ?? 0;
    counts.set(base, n + 1);
    const key = n === 0 ? base : `${base}_${n + 1}`;
    fields[key] = elementValue(el, elCtx);
  }
  return fields;
}

/** Row sub-header: NM1 party role, otherwise X12 segment name. */
export function segmentDisplayLabel(segment: EDI837DecodedSegment): string {
  if (segment.segment_id === "NM1" && segment.segment_label?.trim()) {
    return segment.segment_label;
  }
  return segment.segment_name?.trim() || segment.segment_id;
}

/** Guide section title for JSON (e.g. ``Transaction header (BHT)`` → ``Transaction header``). */
export function normalizeSectionTitleForLabel(title: string): string {
  const t = title.trim();
  if (!t || t.toLowerCase() === "all segments") return "";
  const paren = t.match(/^(.+?)\s*\([^)]+\)\s*$/);
  if (paren?.[1]) return paren[1].trim();
  return t;
}

export function jsonSegmentLabel(
  segment: EDI837DecodedSegment,
  sectionTitle: string
): string {
  const guide = normalizeSectionTitleForLabel(sectionTitle);
  if (guide) return guide;
  return segmentDisplayLabel(segment);
}

function sectionToDict(
  section: EDI837GuideSection,
  exportCtx: Omit<ElementExportCtx, "sequence" | "segment_id">
) {
  return {
    section_id: section.section_id,
    title: section.title,
    segments: section.segments.map((seg) => ({
      segment_id: seg.segment_id,
      sequence: seg.sequence,
      segment_name: seg.segment_name,
      segment_label: jsonSegmentLabel(seg, section.title),
      guide_section_title: section.title,
      party_code: seg.party_code ?? null,
      fields: segmentFields(seg, exportCtx),
    })),
  };
}

export function build837ExportJson(
  decoded: EDI837DecodePayload,
  filename?: string,
  transforms?: Edi837TransformMap
): Record<string, unknown> {
  return {
    format: "x12_837_decode",
    guide_reference: decoded.guide_reference,
    exported_at: new Date().toISOString(),
    active_file: filename ?? decoded.files[0]?.filename ?? null,
    files: decoded.files.map((file) => ({
      filename: file.filename,
      messages: file.messages.map((msg) => {
        const exportCtx = {
          filename: file.filename,
          control_id: msg.control_id,
          transforms,
        };
        return {
          source_file: msg.source_file,
          transaction_set: msg.transaction_set,
          implementation_guide: msg.implementation_guide,
          control_id: msg.control_id,
          sections: (msg.sections?.length
            ? msg.sections
            : [{ section_id: "all", title: "All segments", segments: msg.segments }]
          ).map((section) => sectionToDict(section, exportCtx)),
        };
      }),
    })),
  };
}

export function download837Json(
  decoded: EDI837DecodePayload,
  filename: string,
  transforms?: Edi837TransformMap
): void {
  const payload = build837ExportJson(decoded, filename, transforms);
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  const base = filename.replace(/\.[^.]+$/, "") || "edi-837";
  anchor.href = url;
  anchor.download = `${base}-mapped.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}
