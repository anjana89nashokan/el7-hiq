import { useState, useRef } from "react";
import { CloudUpload, FileSpreadsheet, Trash2 } from "lucide-react";

/* interface PreviewPayload {
  columns: string[];
  rows: any[];
  fileName: string;
  fileSize: number;
  totalRows: number;
  totalColumns: number;
} */

export default function FileUploader({
  onUpload,
  onRemove,
  onUploadFiles,
  multiple = true,
  files,
  allowedTypes,
  maxSize,
  maxFiles,
  profilingInProgress = false,
  uploadButtonLabel,
}: Readonly<{
  onUpload: (files: File[]) => void;
  onRemove?: (index: number) => void;
  onUploadFiles?: () => void;
  multiple?: boolean;
  files: File[];
  allowedTypes?: string[];
  maxSize?: number;
  maxFiles?: number;
  profilingInProgress?: boolean;
  uploadButtonLabel?: string;
}>) {
  const [isDragOver, setIsDragOver] = useState(false);
  const [error, setError] = useState<string>("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const defaultAllowedTypes = [
    ".csv",
    ".tsv",
    ".ced",
    ".json",
    ".xml",
    ".xlsx",
    ".xls",
    ".psv",
    ".txt",
    ".zip",
    ".dat",
    ".fwf",
    ".asc",
    ".prn",
    ".out",
    ".log",
    ".data",
    ".hl7"
  ];
  const fileTypes = allowedTypes || defaultAllowedTypes;
  const fileSizeLimit = maxSize || 100 * 1024 * 1024; // 100MB

  const processFiles = (fileList: FileList) => {
    if (profilingInProgress) {
      setError("<b>Upload disabled:</b> Cannot upload files while profiling is in progress");
      return;
    }
    setError("");
    const fileArray = Array.from(fileList).slice(
      0,
      multiple ? fileList.length : 1
    );

    const maxFileLimit = maxFiles || 5;
    if (files.length + fileArray.length > maxFileLimit) {
      setError(`<b>Too many files:</b> Maximum ${maxFileLimit} files allowed`);
      return;
    }

    for (const file of fileArray) {
      const fileExt = "." + file.name.split(".").pop()?.toLowerCase();
      if (!fileTypes.includes(fileExt)) {
        setError(
          `<b>Invalid file type:</b> ${file.name}. <b>Allowed:</b> ${fileTypes.join(", ")}`
        );
        return;
      }
      if (file.size > fileSizeLimit) {
        setError(
          `<b>File too large:</b> ${file.name}. <b>Maximum size:</b> ${fileSizeLimit > 1024 * 1024 * 1024 ? (fileSizeLimit / (1024 * 1024 * 1024)).toFixed(0) + 'GB' : (fileSizeLimit / (1024 * 1024)).toFixed(0) + 'MB'}`
        );
        return;
      }
    }

    onUpload(fileArray);
  };



  const handleFiles = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (profilingInProgress) {
      setError("<b>Upload disabled:</b> Cannot upload files while profiling is in progress");
      return;
    }
    if (!e.target.files) return;
    processFiles(e.target.files);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    if (profilingInProgress) {
      setError("<b>Upload disabled:</b> Cannot upload files while profiling is in progress");
      return;
    }
    if (e.dataTransfer.files) {
      processFiles(e.dataTransfer.files);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  };

  const handleDragLeave = () => {
    setIsDragOver(false);
  };

  const removeFile = (index: number) => {
    onRemove?.(index);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  return (
    <div>
      <div className="grid grid-cols-2 gap-6 mt-6">
        {/* Left: Upload Box */}
        <label
          htmlFor="file-upload"
          className={`border-2 border-dashed rounded-lg flex flex-col justify-center items-center p-6 text-center transition-colors ${
            profilingInProgress 
              ? "border-gray-300 bg-gray-100 cursor-not-allowed" 
              : isDragOver 
                ? "border-brand-primary bg-brand-surface cursor-pointer" 
                : "border-brand-darkblue cursor-pointer"
          }`}
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
        >
          <input
            ref={fileInputRef}
            type="file"
            multiple={multiple}
            onChange={handleFiles}
            className="hidden"
            id="file-upload"
            disabled={profilingInProgress}
          />
          <div className={`p-2 rounded shadow-lg shadow-gray-400 ${
            profilingInProgress ? "bg-gray-400" : "bg-brand-darkblue"
          }`}>
            <CloudUpload size={32} strokeWidth={1.25} className="text-white" />
          </div>
          <span className={`text-xs mt-4 ${
            profilingInProgress ? "text-gray-500" : "text-brand-darkblue"
          }`}>
            {profilingInProgress 
              ? "Upload disabled during profiling" 
              : `Drag and Drop or Upload ${multiple ? "File(s)" : "File"}`
            }
          </span>
          <p className="text-font-dark/50 text-[10px] mt-1">
            CSV, TSV, JSON, XML, Excel, ZIP, DAT, Fixed-Width, and HL7 (.hl7) message files
          </p>
          <p className="text-[10px] text-font-dark/50 mt-2">
            Maximum file size {fileSizeLimit > 1024 * 1024 * 1024 ? (fileSizeLimit / (1024 * 1024 * 1024)).toFixed(0) + 'GB' : (fileSizeLimit / (1024 * 1024)).toFixed(0) + 'MB'}
          </p>
        </label>

        {/* Right: File List */}
        <div className="border-2 border-dashed border-brand-darkblue rounded-lg p-4">
          <h3 className="text-brand-darkblue mb-2">Uploads</h3>
          <ul
            className={`space-y-1 text-sm ${
              files.length > 3 ? "max-h-[100px] overflow-y-auto" : ""
            }`}
          >
            {files.map((f, i) => (
              <li key={i} className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <FileSpreadsheet
                    size={12}
                    strokeWidth={1.25}
                    className="text-font-dark"
                  />
                  <span className="truncate max-w-[300px] text-xs" title={f.name}>{f.name}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-gray-500 text-xs">{(f.size / 1024).toFixed(1)} KB</span>

                  <button 
                    onClick={() => removeFile(i)} 
                    disabled={profilingInProgress}
                    className={`p-1 rounded-full transition-colors ${
                      profilingInProgress 
                        ? 'cursor-not-allowed opacity-50' 
                        : 'hover:bg-gray-100 cursor-pointer'
                    }`}
                  >
                    <Trash2 size={12} strokeWidth={1.25} className={`text-font-dark ${
                      profilingInProgress ? '' : 'hover:text-red-600'
                    }`} />
                  </button>
                </div>
              </li>
            ))}
          </ul>
          {files.length > 0 && (
            <button
              onClick={onUploadFiles}
              disabled={profilingInProgress}
              className={`mt-4 w-full py-2 rounded-lg transition-colors duration-200 ${
                profilingInProgress 
                  ? "bg-gray-400 text-gray-600 cursor-not-allowed" 
                  : "bg-brand-darkblue text-white cursor-pointer"
              }`}
            >
              {profilingInProgress
                ? uploadButtonLabel || "Upload disabled"
                : "Upload Files"}
            </button>
          )}
        </div>
      </div>
      {error && (
        <div className="mt-2 px-4 py-2 bg-red-50 border border-red-200 rounded-md">
          <p className="text-red-600 text-xs" dangerouslySetInnerHTML={{__html: error}}></p>
        </div>
      )}
    </div>
  );
}
