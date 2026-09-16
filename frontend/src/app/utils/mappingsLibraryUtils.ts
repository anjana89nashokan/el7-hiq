import type { FieldMapping, HL7SessionListItem, PackageVersion } from "../end-points/hl7Api";

export type MappingSetStatus = "approved" | "in_review" | "pending";
export type GroupByOption = "fileType" | "sender" | "status" | "updated";

export interface MappingVersion {
  version: string;
  isCurrent: boolean;
  publishedAt: string;
  publishedBy: string;
  summary: string;
  approved: number;
  total: number;
  packageVersion?: number;
}

export interface MappingLibraryItem {
  id: string;
  hl7SessionId?: string;
  fileType: string;
  category: string;
  sender: string;
  status: MappingSetStatus;
  messages: number;
  approved: number;
  total: number;
  updated: string;
  versions: MappingVersion[];
  isDemo?: boolean;
}

export interface FieldMappingRow {
  source: string;
  target: string;
  transform: string;
  approved: boolean;
}

export const STATUS_LABEL: Record<MappingSetStatus, string> = {
  approved: "Approved",
  in_review: "In review",
  pending: "Pending",
};

export const GROUP_ORDER: Partial<Record<GroupByOption, string[]>> = {
  status: ["approved", "in_review", "pending"],
  updated: ["Today", "Yesterday", "This week", "Earlier"],
};

const PUBLISHERS = ["M. Alvarez", "R. Chen", "J. Okafor", "S. Patel", "L. Novak"];
const CHANGE_SUMMARIES = [
  "Initial approved mapping set",
  "Added missing optional segment mappings",
  "Fixed date/timezone handling on timestamp fields",
  "Updated lookup table for status codes",
  "Refined address field structuring",
  "Adjusted numeric type casting for financial fields",
  "Added Z-segment support",
  "Corrected reference range parsing",
];

export const FIELD_TEMPLATES: Record<string, FieldMappingRow[]> = {
  ADT: [
    { source: "MSH-9", target: "messageType", transform: "Direct passthrough", approved: true },
    { source: "PID-3", target: "memberId", transform: "MRN extracted from repeating ID field", approved: true },
    { source: "PID-5", target: "memberName", transform: "Concatenated given + family", approved: true },
    { source: "PID-7", target: "dateOfBirth", transform: "Reformatted YYYYMMDD → ISO 8601", approved: true },
    { source: "PID-8", target: "gender", transform: "Mapped to standard code set", approved: true },
    { source: "PID-11", target: "address", transform: "Structured into address object", approved: true },
    { source: "PV1-2", target: "encounterClass", transform: "Lookup table mapped", approved: true },
    { source: "PV1-3", target: "facilityLocation", transform: "Direct passthrough", approved: true },
    { source: "PV1-44", target: "admitDateTime", transform: "HL7 TS reformatted to ISO 8601", approved: true },
  ],
  ORU: [
    { source: "MSH-9", target: "messageType", transform: "Direct passthrough", approved: true },
    { source: "OBR-4", target: "orderCode", transform: "Lookup table mapped", approved: true },
    { source: "OBR-7", target: "observedAt", transform: "HL7 TS reformatted to ISO 8601", approved: true },
    { source: "OBX-3", target: "resultCode", transform: "Direct passthrough", approved: true },
    { source: "OBX-5", target: "resultValue", transform: "Type-cast by value type", approved: true },
    { source: "OBX-6", target: "resultUnit", transform: "UCUM unit normalization", approved: true },
    { source: "OBX-7", target: "referenceRange", transform: "Split into low/high bounds", approved: true },
    { source: "OBX-8", target: "abnormalFlag", transform: "Lookup table mapped", approved: true },
    { source: "OBX-11", target: "resultStatus", transform: "Direct passthrough", approved: true },
  ],
  MDM: [
    { source: "MSH-9", target: "messageType", transform: "Direct passthrough", approved: true },
    { source: "TXA-2", target: "documentType", transform: "Lookup table mapped", approved: true },
    { source: "TXA-4", target: "activityDateTime", transform: "HL7 TS reformatted to ISO 8601", approved: true },
    { source: "TXA-12", target: "documentId", transform: "Direct passthrough", approved: true },
    { source: "TXA-17", target: "availabilityStatus", transform: "Lookup table mapped", approved: true },
    { source: "OBX-5", target: "documentContent", transform: "Base64 decoded, concatenated", approved: true },
    { source: "PID-3", target: "memberId", transform: "MRN extracted from repeating ID field", approved: true },
    { source: "PID-5", target: "memberName", transform: "Concatenated given + family", approved: true },
    { source: "PV1-3", target: "facilityLocation", transform: "Direct passthrough", approved: true },
  ],
  SIU: [
    { source: "MSH-9", target: "messageType", transform: "Direct passthrough", approved: true },
    { source: "SCH-1", target: "appointmentId", transform: "Direct passthrough", approved: true },
    { source: "SCH-11", target: "appointmentTime", transform: "HL7 TS reformatted to ISO 8601", approved: true },
    { source: "SCH-25", target: "appointmentStatus", transform: "Lookup table mapped", approved: true },
    { source: "AIS-3", target: "serviceCode", transform: "Direct passthrough", approved: true },
    { source: "AIP-3", target: "providerName", transform: "Concatenated given + family", approved: true },
    { source: "RGS-3", target: "resourceGroup", transform: "Direct passthrough", approved: true },
    { source: "PID-3", target: "memberId", transform: "MRN extracted from repeating ID field", approved: true },
    { source: "PID-5", target: "memberName", transform: "Concatenated given + family", approved: true },
  ],
  DFT: [
    { source: "MSH-9", target: "messageType", transform: "Direct passthrough", approved: true },
    { source: "FT1-6", target: "transactionCode", transform: "Lookup table mapped", approved: true },
    { source: "FT1-7", target: "transactionDescription", transform: "Direct passthrough", approved: true },
    { source: "FT1-10", target: "quantity", transform: "Type-cast to number", approved: true },
    { source: "FT1-11", target: "amount", transform: "Type-cast to decimal", approved: true },
    { source: "FT1-19", target: "diagnosisCode", transform: "ICD-10 code validated", approved: true },
    { source: "PID-3", target: "memberId", transform: "MRN extracted from repeating ID field", approved: true },
    { source: "PID-5", target: "memberName", transform: "Concatenated given + family", approved: true },
    { source: "PV1-3", target: "facilityLocation", transform: "Direct passthrough", approved: true },
  ],
};

const DEMO_MAPPINGS_BASE: Omit<MappingLibraryItem, "versions">[] = [
  { id: "lib_01", fileType: "MDM^T02", category: "MDM", sender: "Epic Systems", status: "approved", messages: 1, approved: 39, total: 39, updated: "2026-08-17T20:30:00" },
  { id: "lib_02", fileType: "MDM^T02", category: "MDM", sender: "Cerner Millennium", status: "approved", messages: 1, approved: 39, total: 39, updated: "2026-08-17T19:53:00" },
  { id: "lib_03", fileType: "MDM^T02", category: "MDM", sender: "Meditech Expanse", status: "in_review", messages: 1, approved: 36, total: 39, updated: "2026-08-17T19:24:00" },
  { id: "lib_04", fileType: "MDM^T02", category: "MDM", sender: "NextGen Healthcare", status: "approved", messages: 1, approved: 35, total: 35, updated: "2026-08-17T18:27:00" },
  { id: "lib_05", fileType: "ORU^R01", category: "ORU", sender: "Epic Systems", status: "approved", messages: 1, approved: 43, total: 43, updated: "2026-08-17T20:20:00" },
  { id: "lib_06", fileType: "ORU^R01", category: "ORU", sender: "Allscripts", status: "in_review", messages: 1, approved: 28, total: 34, updated: "2026-08-16T15:10:00" },
  { id: "lib_07", fileType: "ORU^R01", category: "ORU", sender: "Cerner Millennium", status: "approved", messages: 1, approved: 20, total: 20, updated: "2026-08-06T08:00:00" },
  { id: "lib_08", fileType: "ADT^A01", category: "ADT", sender: "Cerner Millennium", status: "approved", messages: 1, approved: 51, total: 51, updated: "2026-08-15T11:05:00" },
  { id: "lib_09", fileType: "ADT^A01", category: "ADT", sender: "Epic Systems", status: "pending", messages: 1, approved: 0, total: 47, updated: "2026-08-14T09:40:00" },
  { id: "lib_10", fileType: "ADT^A01", category: "ADT", sender: "NextGen Healthcare", status: "approved", messages: 1, approved: 29, total: 29, updated: "2026-08-05T11:11:00" },
  { id: "lib_11", fileType: "ADT^A08", category: "ADT", sender: "Meditech Expanse", status: "approved", messages: 1, approved: 44, total: 44, updated: "2026-08-13T16:15:00" },
  { id: "lib_12", fileType: "ADT^A08", category: "ADT", sender: "NextGen Healthcare", status: "in_review", messages: 1, approved: 30, total: 40, updated: "2026-08-12T14:00:00" },
  { id: "lib_13", fileType: "SIU^S12", category: "SIU", sender: "Epic Systems", status: "approved", messages: 1, approved: 22, total: 22, updated: "2026-08-11T10:30:00" },
  { id: "lib_14", fileType: "SIU^S12", category: "SIU", sender: "Cerner Millennium", status: "in_review", messages: 1, approved: 18, total: 25, updated: "2026-08-10T13:45:00" },
  { id: "lib_15", fileType: "DFT^P03", category: "DFT", sender: "Allscripts", status: "approved", messages: 1, approved: 33, total: 33, updated: "2026-08-09T17:20:00" },
  { id: "lib_16", fileType: "DFT^P03", category: "DFT", sender: "Meditech Expanse", status: "pending", messages: 1, approved: 0, total: 29, updated: "2026-08-08T09:00:00" },
];

export function buildDemoVersions(item: Omit<MappingLibraryItem, "versions">): MappingVersion[] {
  if (item.status !== "approved") return [];
  const seed = parseInt(item.id.replace(/\D/g, ""), 10) || 1;
  const count = 2 + (seed % 3);
  const versions: MappingVersion[] = [];
  let publishDate = new Date(item.updated);
  for (let i = count; i >= 1; i -= 1) {
    const isCurrent = i === count;
    versions.push({
      version: `v${i}`,
      isCurrent,
      publishedAt: publishDate.toISOString(),
      publishedBy: PUBLISHERS[(seed + i) % PUBLISHERS.length],
      summary: isCurrent
        ? CHANGE_SUMMARIES[seed % CHANGE_SUMMARIES.length]
        : CHANGE_SUMMARIES[(seed + i * 2) % CHANGE_SUMMARIES.length],
      approved: isCurrent ? item.approved : Math.max(1, item.approved - (count - i) * 2),
      total: item.total,
    });
    publishDate = new Date(publishDate.getTime() - (6 + i * 2) * 86400000);
  }
  return versions;
}

export const DEMO_MAPPINGS: MappingLibraryItem[] = DEMO_MAPPINGS_BASE.map((item) => ({
  ...item,
  isDemo: true,
  versions: buildDemoVersions(item),
}));

export function fmtDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "Not available";
  return (
    d.toLocaleDateString("en-US", { month: "numeric", day: "numeric", year: "numeric" }) +
    ", " +
    d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })
  );
}

export function updatedBucket(iso: string, now = new Date()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "Earlier";
  const diffMs = now.getTime() - d.getTime();
  const diffDays = diffMs / 86400000;
  if (now.toDateString() === d.toDateString()) return "Today";
  if (diffDays < 2) return "Yesterday";
  if (diffDays < 7) return "This week";
  return "Earlier";
}

export function groupKey(item: MappingLibraryItem, groupBy: GroupByOption): string {
  if (groupBy === "fileType") return item.fileType;
  if (groupBy === "sender") return item.sender;
  if (groupBy === "status") return item.status;
  if (groupBy === "updated") return updatedBucket(item.updated);
  return "All";
}

export function groupLabel(key: string, groupBy: GroupByOption): string {
  if (groupBy === "status") return STATUS_LABEL[key as MappingSetStatus] || key;
  return key;
}

export function packageVersionsToLibraryVersions(
  versions: PackageVersion[],
  total: number,
): MappingVersion[] {
  if (!versions.length) return [];
  const sorted = [...versions].sort((a, b) => b.version - a.version);
  return sorted.map((v, index) => ({
    version: `v${v.version}`,
    isCurrent: index === 0,
    publishedAt: v.published_at,
    publishedBy: v.published_by || "System",
    summary: v.note?.trim() || "Published mapping package",
    approved: v.mapping_count,
    total,
    packageVersion: v.version,
  }));
}

export function hl7SessionToLibraryItem(session: HL7SessionListItem): MappingLibraryItem {
  const fileTypes = Object.keys(session.message_types);
  const fileType = fileTypes.join(", ") || "HL7";
  const category = fileTypes[0]?.split("^")[0] || "HL7";

  let status: MappingSetStatus = "pending";
  if (session.status.startsWith("published")) status = "approved";
  else if (session.status === "in review") status = "in_review";

  const updated = session.published_at || session.created_at || new Date().toISOString();
  const versions: MappingVersion[] =
    session.latest_version != null
      ? [
          {
            version: `v${session.latest_version}`,
            isCurrent: true,
            publishedAt: session.published_at || updated,
            publishedBy: "System",
            summary: "Published mapping package",
            approved: session.mappings_approved,
            total: session.mappings_total,
            packageVersion: session.latest_version,
          },
        ]
      : [];

  return {
    id: session.hl7_session_id,
    hl7SessionId: session.hl7_session_id,
    fileType,
    category,
    sender: "Uploaded source",
    status,
    messages: session.messages_parsed,
    approved: session.mappings_approved,
    total: session.mappings_total,
    updated,
    versions,
  };
}

export function mergeLibraryItems(
  sessions: HL7SessionListItem[],
  includeDemoSeed = true,
): MappingLibraryItem[] {
  const fromSessions = sessions.map(hl7SessionToLibraryItem);
  if (!includeDemoSeed) return fromSessions;
  const sessionIds = new Set(fromSessions.map((item) => item.id));
  const demoItems = DEMO_MAPPINGS.filter((item) => !sessionIds.has(item.id));
  return [...fromSessions, ...demoItems];
}

export function fieldMappingsToRows(
  mappings: FieldMapping[],
  approvedCount: number,
  total: number,
): FieldMappingRow[] {
  const sorted = [...mappings].sort((a, b) => a.source_path.localeCompare(b.source_path, undefined, { numeric: true }));
  const sample = sorted.slice(0, 9);
  const approvedRatio = total ? approvedCount / total : 0;
  const approvedRows = Math.round(sample.length * approvedRatio);
  return sample.map((mapping, index) => ({
    source: mapping.source_path,
    target: mapping.target_path,
    transform: mapping.transformation || "Direct passthrough",
    approved: mapping.status === "approved" || index < approvedRows,
  }));
}

export function templateFieldRows(
  item: MappingLibraryItem,
  version?: MappingVersion | null,
): FieldMappingRow[] {
  const approved = version ? version.approved : item.approved;
  const total = version ? version.total : item.total;
  const template = FIELD_TEMPLATES[item.category] || FIELD_TEMPLATES.ADT;
  const approvedRows = Math.round(template.length * (total ? approved / total : item.status === "approved" ? 1 : 0));
  return template.map((row, index) => ({
    ...row,
    approved: index < approvedRows,
  }));
}

export function librarySnapshotStats(items: MappingLibraryItem[]) {
  return {
    fileTypes: new Set(items.map((item) => item.fileType)).size,
    senders: new Set(items.map((item) => item.sender)).size,
    sets: items.length,
  };
}
