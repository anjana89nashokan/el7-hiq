import { useState } from "react";
import type { ExtractFormState } from "../interfaces/extract";

interface UseExtractFormReturn {
  form: ExtractFormState;
  fieldErrors: Record<string, string>;
  submitAttempted: boolean;
  noSessionError: boolean;
  setFileField: (key: string, file: File | null, err: string | null) => void;
  setInterfaceCode: (value: string) => void;
  setBsaNotes: (value: string) => void;
  validate: () => Record<string, string>;
  setSubmitAttempted: (v: boolean) => void;
  setNoSessionError: (v: boolean) => void;
  setFieldErrors: React.Dispatch<React.SetStateAction<Record<string, string>>>;
  reset: () => void;
}

const INITIAL_FORM: ExtractFormState = {
  brdFile: null,
  fileLayoutFile: null,
  transcriptFile: null,
  interfaceCode: "",
  bsaNotes: "",
};

export function useExtractForm(): UseExtractFormReturn {
  const [form, setForm] = useState<ExtractFormState>(INITIAL_FORM);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [submitAttempted, setSubmitAttempted] = useState(false);
  const [noSessionError, setNoSessionError] = useState(false);

  const setFileField = (key: string, file: File | null, err: string | null) => {
    setForm((prev) => ({
      ...prev,
      ...(key === "brd" && { brdFile: file }),
      ...(key === "layout" && { fileLayoutFile: file }),
      ...(key === "transcript" && { transcriptFile: file }),
    }));
    setFieldErrors((prev) => {
      const next = { ...prev };
      if (err) next[key] = err; else delete next[key];
      return next;
    });
  };

  const setInterfaceCode = (value: string) => {
    setForm((prev) => ({ ...prev, interfaceCode: value }));
    setFieldErrors((prev) => {
      const next = { ...prev };
      if (value.trim()) delete next.interfaceCode;
      return next;
    });
  };

  const setBsaNotes = (value: string) => setForm((prev) => ({ ...prev, bsaNotes: value }));

  const validate = (): Record<string, string> => {
    const errors: Record<string, string> = {};
    if (!form.brdFile) errors.brd = "BRD Document is required.";
    if (!form.fileLayoutFile) errors.layout = "File Layout Document is required.";
    if (!form.interfaceCode.trim()) errors.interfaceCode = "Interface Code is required.";
    return errors;
  };

  const reset = () => {
    setForm(INITIAL_FORM);
    setFieldErrors({});
    setSubmitAttempted(false);
    setNoSessionError(false);
  };

  return {
    form,
    fieldErrors,
    submitAttempted,
    noSessionError,
    setFileField,
    setInterfaceCode,
    setBsaNotes,
    validate,
    setSubmitAttempted,
    setNoSessionError,
    setFieldErrors,
    reset,
  };
}
