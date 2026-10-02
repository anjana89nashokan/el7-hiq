import type {
  EDI837DecodedElement,
  EDI837DecodePayload,
  EDI837DecodedSegment,
  EDI837GuideSection,
} from "../end-points/ediApi";

function targetKey(el: EDI837DecodedElement): string {
  return el.target || el.element_id.replace(/-/g, "_").toLowerCase();
}

function elementValue(el: EDI837DecodedElement): unknown {
  if (el.children?.length) {
    const nested: Record<string, unknown> = {};
    for (const child of el.children) {
      nested[targetKey(child)] = child.value;
    }
    if (el.value?.trim()) {
      nested._raw = el.value;
    }
    return nested;
  }
  return el.value;
}

function segmentFields(segment: EDI837DecodedSegment): Record<string, unknown> {
  const fields: Record<string, unknown> = {};
  const counts = new Map<string, number>();
  for (const el of segment.elements) {
    const base = targetKey(el);
    const n = counts.get(base) ?? 0;
    counts.set(base, n + 1);
    const key = n === 0 ? base : `${base}_${n + 1}`;
    fields[key] = elementValue(el);
  }
  return fields;
}

function sectionToDict(section: EDI837GuideSection) {
  return {
    section_id: section.section_id,
    title: section.title,
    segments: section.segments.map((seg) => ({
      segment_id: seg.segment_id,
      sequence: seg.sequence,
      segment_label: seg.segment_label ?? null,
      party_code: seg.party_code ?? null,
      fields: segmentFields(seg),
    })),
  };
}

export function build837ExportJson(
  decoded: EDI837DecodePayload,
  filename?: string
): Record<string, unknown> {
  return {
    format: "x12_837_decode",
    guide_reference: decoded.guide_reference,
    exported_at: new Date().toISOString(),
    active_file: filename ?? decoded.files[0]?.filename ?? null,
    files: decoded.files.map((file) => ({
      filename: file.filename,
      messages: file.messages.map((msg) => ({
        source_file: msg.source_file,
        transaction_set: msg.transaction_set,
        implementation_guide: msg.implementation_guide,
        control_id: msg.control_id,
        sections: (msg.sections?.length
          ? msg.sections
          : [{ section_id: "all", title: "All segments", segments: msg.segments }]
        ).map(sectionToDict),
      })),
    })),
  };
}

export function download837Json(
  decoded: EDI837DecodePayload,
  filename: string
): void {
  const payload = build837ExportJson(decoded, filename);
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  const base = filename.replace(/\.[^.]+$/, "") || "edi-837";
  anchor.href = url;
  anchor.download = `${base}-mapped.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}
