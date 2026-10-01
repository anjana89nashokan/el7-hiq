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
  return response.data as HL7Result & { format?: string };
};

export const getEdiCanonicalModel = async (): Promise<CanonicalEntity[]> => {
  const response = await axiosInstance.get("/edi/canonical-model");
  return response.data.entities as CanonicalEntity[];
};
