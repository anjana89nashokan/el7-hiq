import { ALLOWED_EXTENSIONS, MAX_FILE_SIZE, MAX_FILE_SIZE_MB } from "../config/extractConfig";

export function validateFile(file: File, allowedExts = ALLOWED_EXTENSIONS): string | null {
  const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
  if (!allowedExts.includes(ext))
    return `Invalid file type "${ext}". Only ${allowedExts.map(e => e.slice(1).toUpperCase()).join(", ")} allowed.`;
  if (file.size > MAX_FILE_SIZE)
    return `File exceeds ${MAX_FILE_SIZE_MB}MB limit.`;
  return null;
}
