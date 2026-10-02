import type {
  EDI837DecodedElement,
  EDI837DecodedMessage,
  EDI837DecodedSegment,
  EDI837DecodePayload,
  EDI837GuideSection,
} from "../end-points/ediApi";

export interface Edi837MappingRow {
  id: string;
  filename: string;
  control_id: string;
  section_id: string;
  section_title: string;
  sequence: number;
  segment_id: string;
  segment_label: string;
  element_id: string;
  element_name: string;
  target: string;
  value: string;
  meaning: string;
}

export function mappingRowId(parts: {
  filename: string;
  control_id: string;
  sequence: number;
  segment_id: string;
  element_id: string;
}): string {
  return `${parts.filename}|${parts.control_id}|${parts.sequence}|${parts.segment_id}|${parts.element_id}`;
}

function walkElements(
  elements: EDI837DecodedElement[],
  ctx: Omit<Edi837MappingRow, "element_id" | "element_name" | "target" | "value" | "meaning" | "id">,
  rows: Edi837MappingRow[]
): void {
  for (const el of elements) {
    if (el.children?.length) {
      walkElements(el.children, ctx, rows);
      continue;
    }
    if (!el.value?.trim() && !el.element_id) continue;
    const id = mappingRowId({
      filename: ctx.filename,
      control_id: ctx.control_id,
      sequence: ctx.sequence,
      segment_id: ctx.segment_id,
      element_id: el.element_id,
    });
    rows.push({
      id,
      ...ctx,
      element_id: el.element_id,
      element_name: el.element_name,
      target: el.target || el.element_id.replace(/-/g, "_").toLowerCase(),
      value: el.value ?? "",
      meaning: el.meaning ?? "",
    });
  }
}

function segmentsFromMessage(msg: EDI837DecodedMessage): EDI837GuideSection[] {
  if (msg.sections?.length) return msg.sections;
  return [{ section_id: "all", title: "All segments", segments: msg.segments }];
}

export function buildEdi837MappingRows(
  decoded: EDI837DecodePayload,
  activeFilename: string
): Edi837MappingRow[] {
  const file = decoded.files.find((f) => f.filename === activeFilename) ?? decoded.files[0];
  if (!file) return [];

  const rows: Edi837MappingRow[] = [];
  for (const msg of file.messages) {
    for (const section of segmentsFromMessage(msg)) {
      for (const seg of section.segments) {
        const base = {
          filename: file.filename,
          control_id: msg.control_id,
          section_id: section.section_id,
          section_title: section.title,
          sequence: seg.sequence,
          segment_id: seg.segment_id,
          segment_label: seg.segment_label || seg.segment_name || seg.segment_id,
        };
        walkElements(seg.elements, base, rows);
      }
    }
  }
  return rows;
}
