import axiosInstance from "../utils/axios-interceptor";
import type { HL7Result, CanonicalEntity } from "./hl7Api";

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
