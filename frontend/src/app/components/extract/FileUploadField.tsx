import { CloudUpload, X, Info } from "lucide-react";
import { ALLOWED_EXTENSIONS, MAX_FILE_SIZE_MB, FIELD_INFO } from "../../config/extractConfig";
import { validateFile } from "../../utils/extractValidation";

interface Props {
  readonly label: string;
  readonly required?: boolean;
  readonly file: File | null;
  readonly error?: string;
  readonly onChange: (file: File | null, error: string | null) => void;
  readonly allowedExts?: string[];
}

export default function FileUploadField({ label, required, file, error, onChange, allowedExts = ALLOWED_EXTENSIONS }: Props) {
  const inputId = `file-${label.replaceAll(" ", "-").toLowerCase()}`;
  const hasError = Boolean(error);
  const acceptAttr = allowedExts.join(",");
  const extLabel = allowedExts.map(e => e.slice(1).toUpperCase()).join(" or ");
  const tooltip = FIELD_INFO[label];

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-1 text-sm font-medium text-gray-700 mb-2">
        {label} {required && <span className="text-red-500">*</span>}
        {tooltip && (
          <span className="relative group ml-1">
            <Info size={13} className="text-gray-400 cursor-pointer hover:text-brand-primary" />
            <span className="absolute left-5 top-0 z-50 hidden group-hover:block w-64 bg-gray-800 text-white text-xs rounded-lg px-3 py-2 shadow-lg">
              {tooltip}
            </span>
          </span>
        )}
      </div>
      <input
        type="file"
        id={inputId}
        accept={acceptAttr}
        className="hidden"
        onChange={(e) => {
          const selected = e.target.files?.[0] ?? null;
          if (!selected) { onChange(null, null); return; }
          const err = validateFile(selected, allowedExts);
          onChange(err ? null : selected, err);
          e.target.value = "";
        }}
      />
      {file ? (
        <div className="border-2 border-brand-darkblue rounded-lg p-3 flex items-center gap-2 bg-brand-surface">
          <CloudUpload size={18} className="text-brand-darkblue shrink-0" />
          <span className="text-xs text-gray-700 truncate flex-1">{file.name}</span>
          <button
            type="button"
            onClick={() => onChange(null, null)}
            className="shrink-0 text-gray-400 hover:text-red-500 transition-colors"
            aria-label="Remove file"
          >
            <X size={14} />
          </button>
        </div>
      ) : (
        <label
          htmlFor={inputId}
          className={`border-2 border-dashed rounded-lg p-3 flex items-center gap-2 cursor-pointer transition-colors ${
            hasError ? "border-red-400 bg-red-50" : "border-brand-darkblue hover:bg-brand-surface"
          }`}
        >
          <CloudUpload size={18} className={hasError ? "text-red-400 shrink-0" : "text-brand-darkblue shrink-0"} />
          <span className={`text-xs truncate ${hasError ? "text-red-500" : "text-gray-600"}`}>
            {`Upload ${label} (${extLabel}, max ${MAX_FILE_SIZE_MB}MB)`}
          </span>
        </label>
      )}
      {hasError && <p className="text-xs text-red-600 mt-1">{error}</p>}
    </div>
  );
}
