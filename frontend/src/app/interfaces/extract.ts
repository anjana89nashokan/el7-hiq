export type ExtractTab = "requirement" | "fileLayout";

export interface ExtractFormState {
  brdFile: File | null;
  fileLayoutFile: File | null;
  transcriptFile: File | null;
  interfaceCode: string;
  bsaNotes: string;
}
