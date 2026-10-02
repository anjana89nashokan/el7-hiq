import axiosInstance from "../utils/axios-interceptor";
import type { HL7Result, CanonicalEntity } from "./hl7Api";

/** File extensions that may carry X12 EDI (``.dat`` is sniffed for ISA/ST). */
export const EDI_EXTENSIONS = [".edi", ".dat"] as const;

export const isEDIFileByName = (file: File): boolean => {
  const lower = file.name.toLowerCase();
  return EDI_EXTENSIONS.some((ext) => lower.endsWith(ext));
};

/** True when content looks like HIPAA X12 (not fixed-width .dat). */
export const looksLikeX12Head = (text: string): boolean => {
  const sample = text.trimStart().slice(0, 4096);
  if (sample.startsWith("ISA")) return true;
  if (sample.startsWith("GS*")) return true;
  if (/^ST\*(835|837|270|271)\*/.test(sample)) return true;
  if (/(?:^|~|\n)ST\*(835|837|270|271)\*/.test(sample)) return true;
  return false;
};

export const sniffEDIFile = async (file: File): Promise<boolean> => {
  const lower = file.name.toLowerCase();
  if (lower.endsWith(".edi")) return true;
  if (!lower.endsWith(".dat")) return false;
  const head = await file.slice(0, 4096).text();
  return looksLikeX12Head(head);
};

/** @deprecated use sniffEDIFile for .dat; kept for quick checks on .edi only */
export const isEDIFile = (file: File): boolean =>
  file.name.toLowerCase().endsWith(".edi");

export const uploadEDIFiles = async (
  files: File[],
  appSessionId?: string | null
): Promise<HL7Result> => {
  const formData = new FormData();
  files.forEach((file) => formData.append("files", file, file.name));
  if (appSessionId) {
    formData.append("app_session_id", appSessionId);
  }

  const response = await axiosInstance.post("/edi/upload", formData);
  return response.data as HL7ResultWithEdi & { format?: string };
};

export const getEdiCanonicalModel = async (): Promise<CanonicalEntity[]> => {
  const response = await axiosInstance.get("/edi/canonical-model");
  return response.data.entities as CanonicalEntity[];
};

export interface EDI837DecodedElement {
  element_id: string;
  element_name: string;
  value: string;
  meaning: string;
  /** Snake_case JSON field name (from element name). */
  target: string;
  /** Composite sub-elements (e.g. HI01-1, HI01-2 per companion guide). */
  children?: EDI837DecodedElement[];
}

export interface EDI837DecodedSegment {
  sequence: number;
  segment_id: string;
  segment_name: string;
  /** Companion-guide party role for NM1 (e.g. Billing Provider). */
  segment_label?: string;
  party_code?: string;
  /** Parent guide section title from decode (e.g. Transaction header (BHT)). */
  guide_section_title?: string;
  elements: EDI837DecodedElement[];
}

export interface EDI837GuideSection {
  section_id: string;
  title: string;
  /** One guide table for LOOP 2300 (HI + claim NM1 rows together). */
  combined_table?: boolean;
  segments: EDI837DecodedSegment[];
}

export interface EDI837DecodedMessage {
  source_file: string;
  transaction_set: string;
  implementation_guide: string;
  guide_reference: string;
  version: string;
  control_id: string;
  segments: EDI837DecodedSegment[];
  sections?: EDI837GuideSection[];
}

export interface EDI837DecodeFile {
  filename: string;
  messages: EDI837DecodedMessage[];
}

export interface EDI837DecodePayload {
  guide_reference: string;
  files: EDI837DecodeFile[];
}

export type HL7ResultWithEdi = HL7Result & {
  view_mode?: "837_decode" | "x12_mapping";
  edi_decoded?: EDI837DecodePayload;
};

export const getEdiDecoded = async (hl7SessionId: string): Promise<EDI837DecodePayload> => {
  const response = await axiosInstance.get(`/edi/sessions/${hl7SessionId}/decoded`);
  return response.data as EDI837DecodePayload;
};

export const exportEdi837Json = async (hl7SessionId: string): Promise<Record<string, unknown>> => {
  const response = await axiosInstance.get(`/edi/sessions/${hl7SessionId}/export/json`);
  return response.data as Record<string, unknown>;
};
