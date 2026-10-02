/** Labels for HL7 vs X12 EDI sessions (stored in hl7_sessions). */

export type InterchangeFormat = "hl7" | "x12" | string | undefined;
export type InterchangeViewMode = "837_decode" | "x12_mapping" | string | undefined;

export interface InterchangeSessionMeta {
  format?: InterchangeFormat;
  view_mode?: InterchangeViewMode;
  message_types?: Record<string, number>;
}

function primaryTransactionType(messageTypes: Record<string, number> | undefined): string {
  const keys = Object.keys(messageTypes ?? {});
  if (!keys.length) return "";
  const sorted = [...keys].sort((a, b) => (messageTypes?.[b] ?? 0) - (messageTypes?.[a] ?? 0));
  return sorted[0].split("^")[0].trim();
}

export function interchangeSessionLabel(meta: InterchangeSessionMeta): string {
  if (meta.format === "x12") {
    const txn = primaryTransactionType(meta.message_types) || "837";
    if (meta.view_mode === "837_decode") {
      return `EDI ${txn} decode`;
    }
    return `X12 EDI ${txn}`;
  }
  const txn = primaryTransactionType(meta.message_types);
  if (txn) {
    return `HL7 ${txn}`;
  }
  return "HL7 v2";
}

export function interchangeAnalysesSectionTitle(sessions: InterchangeSessionMeta[]): string {
  const hasEdi = sessions.some((s) => s.format === "x12");
  const hasHl7 = sessions.some((s) => s.format !== "x12");
  if (hasEdi && hasHl7) return "Interchange analyses";
  if (hasEdi) return "EDI analyses";
  return "HL7 analyses";
}

export function isEdi837Decode(meta: InterchangeSessionMeta): boolean {
  return meta.format === "x12" && meta.view_mode === "837_decode";
}

export function interchangeOpenActionLabel(meta: InterchangeSessionMeta): string {
  return isEdi837Decode(meta) ? "Decode" : meta.format === "x12" ? "Profile" : "Profile";
}

/** Second sidebar action: HL7 mapping review vs EDI 837 JSON mapping. */
export function interchangeMappingPath(hl7SessionId: string, meta: InterchangeSessionMeta): string {
  if (isEdi837Decode(meta)) {
    return `/hl7/${hl7SessionId}/json-mapping`;
  }
  return `/hl7/${hl7SessionId}/review`;
}

export function interchangeMappingActionLabel(
  meta: InterchangeSessionMeta & { mapping_complete?: boolean }
): string {
  if (isEdi837Decode(meta)) {
    return "Mapping";
  }
  return meta.mapping_complete ? "Mapped" : "Review";
}

export function interchangeMappingActionTitle(meta: InterchangeSessionMeta): string {
  if (isEdi837Decode(meta)) {
    return "Open JSON mapping";
  }
  return "Open mapping review";
}
