export const extractFileName = (response: Response, url: string, index: number): string => {
  let fileName = 'unknown-file';
  
  // Try Content-Disposition header first
  const contentDisposition = response.headers.get('Content-Disposition');
  if (contentDisposition) {
    const match = contentDisposition.match(/filename[*]?=["']?([^"';]+)["']?/i);
    if (match) {
      return decodeURIComponent(match[1]).replace(/[<>:"/\\|?*]/g, '_');
    }
  }
  
  // Fallback to URL path extraction
  try {
    const urlObj = new URL(url);
    const pathParts = urlObj.pathname.split('/').filter(part => part.length > 0);
    fileName = pathParts.pop() || `file-${index + 1}`;
  } catch {
    fileName = `file-${index + 1}`;
  }
  
  // Add extension if missing
  if (!fileName.includes('.')) {
    const contentType = response.headers.get('Content-Type') || '';
    if (contentType.includes('pdf')) fileName += '.pdf';
    else if (contentType.includes('excel') || contentType.includes('spreadsheet')) fileName += '.xlsx';
    else if (contentType.includes('csv')) fileName += '.csv';
    else if (contentType.includes('json')) fileName += '.json';
    else fileName += '.bin';
  }
  
  return fileName.replace(/[<>:"/\\|?*]/g, '_');
};

export const downloadBlob = (blob: Blob, fileName: string): void => {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = fileName;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
};

export const createTimestampedFileName = (baseName: string, extension: string): string => {
  const timestamp = new Date().toISOString().split('T')[0];
  return `${baseName}_${timestamp}.${extension}`;
};

export const validateFileSize = (size: number, maxSize: number = 100 * 1024 * 1024): boolean => {
  return size > 0 && size <= maxSize;
};

export const delay = (ms: number): Promise<void> => {
  return new Promise(resolve => setTimeout(resolve, ms));
};