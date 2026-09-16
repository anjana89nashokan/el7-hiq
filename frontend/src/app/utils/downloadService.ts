import { zip } from 'fflate';
import { fetchFile } from '../end-points/downloadApi';
import { 
  extractFileName, 
  downloadBlob, 
  createTimestampedFileName, 
  validateFileSize, 
  delay 
} from './fileUtils';
import { createProgressModal, type ModalControls } from './downloadModals';

export class DownloadService {
  private progressModal: ModalControls | null = null;

  /**
   * Downloads files individually with progress tracking
   * @param urls Array of file URLs to download
   */
  async downloadIndividually(urls: string[]): Promise<void> {
    if (urls.length === 0) {
      console.warn('No URLs provided for download');
      return;
    }

    this.progressModal = createProgressModal();
    let downloadCount = 0;

    try {
      for (let i = 0; i < urls.length; i++) {
        const url = urls[i];
        this.updateProgress(i + 1, urls.length, `Downloading file ${i + 1} of ${urls.length}...`);
        
        try {
          const response = await fetchFile(url);
          const blob = await response.blob();
          
          if (!validateFileSize(blob.size)) {
            console.warn(`Skipping file ${i + 1}: Invalid size (${blob.size} bytes)`);
            continue;
          }
          
          const fileName = extractFileName(response, url, i);
          downloadBlob(blob, fileName);
          downloadCount++;
          
          // Add delay between downloads to prevent overwhelming the browser
          if (i < urls.length - 1) {
            await delay(500);
          }
        } catch (error) {
          console.error(`Failed to download file ${i + 1} (${url}):`, error);
        }
      }
      
      this.showDownloadResult(downloadCount, urls.length);
    } catch (error) {
      console.error('Download process failed:', error);
      this.showError(`Failed to download files: ${error instanceof Error ? error.message : 'Unknown error'}`);
    } finally {
      this.cleanup();
    }
  }

  /**
   * Downloads multiple files as a single ZIP archive
   * @param urls Array of file URLs to include in ZIP
   */
  async downloadAsZip(urls: string[]): Promise<void> {
    if (urls.length === 0) {
      console.warn('No URLs provided for ZIP download');
      return;
    }

    this.progressModal = createProgressModal();
    const files: Record<string, Uint8Array> = {};

    try {
      // Fetch all files
      for (let i = 0; i < urls.length; i++) {
        const url = urls[i];
        this.updateProgress(i + 1, urls.length, `Fetching file ${i + 1} of ${urls.length}...`);
        
        try {
          const response = await fetchFile(url);
          const blob = await response.blob();
          
          if (!validateFileSize(blob.size)) {
            console.warn(`Skipping file ${i + 1}: Invalid size (${blob.size} bytes)`);
            continue;
          }
          
          const fileName = extractFileName(response, url, i);
          const arrayBuffer = await blob.arrayBuffer();
          files[fileName] = new Uint8Array(arrayBuffer);
        } catch (error) {
          console.error(`Failed to fetch file ${i + 1} (${url}):`, error);
        }
      }
      
      if (Object.keys(files).length === 0) {
        this.showError('No files could be downloaded for the ZIP archive.');
        return;
      }
      
      // Create ZIP file
      this.updateProgress(urls.length, urls.length, 'Creating ZIP file...');
      await this.createZipFile(files);
      
    } catch (error) {
      console.error('ZIP download process failed:', error);
      this.showError(`Failed to create ZIP: ${error instanceof Error ? error.message : 'Unknown error'}`);
    } finally {
      this.cleanup();
    }
  }

  /**
   * Creates and downloads a ZIP file from the provided files
   * @param files Record of filename to file data mappings
   */
  private async createZipFile(files: Record<string, Uint8Array>): Promise<void> {
    return new Promise((resolve, reject) => {
      zip(files, (error, data) => {
        if (error) {
          reject(new Error(`ZIP creation failed: ${error.message}`));
          return;
        }
        
        try {
          const blob = new Blob([data], { type: 'application/zip' });
          const fileName = createTimestampedFileName('files', 'zip');
          downloadBlob(blob, fileName);
          
          this.showSuccess(`Successfully created ZIP with ${Object.keys(files).length} file(s).`);
          resolve();
        } catch (downloadError) {
          reject(new Error(`ZIP download failed: ${downloadError instanceof Error ? downloadError.message : 'Unknown error'}`));
        }
      });
    });
  }

  /**
   * Updates the progress modal with current status
   * @param current Current progress count
   * @param total Total items to process
   * @param message Status message to display
   */
  private updateProgress(current: number, total: number, message: string): void {
    if (this.progressModal) {
      this.progressModal.updateProgress(current, total, message);
    }
  }

  /**
   * Shows download completion result to user
   * @param downloadCount Number of successfully downloaded files
   * @param totalCount Total number of files attempted
   */
  private showDownloadResult(downloadCount: number, totalCount: number): void {
    if (downloadCount === 0) {
      this.showError('No files could be downloaded.');
    } else if (downloadCount === totalCount) {
      this.showSuccess(`Successfully downloaded all ${downloadCount} file(s).`);
    } else {
      this.showWarning(`Downloaded ${downloadCount} of ${totalCount} file(s). Some files failed to download.`);
    }
  }

  /**
   * Shows success message to user
   * @param message Success message to display
   */
  private showSuccess(message: string): void {
    alert(`✅ ${message}`);
  }

  /**
   * Shows warning message to user
   * @param message Warning message to display
   */
  private showWarning(message: string): void {
    alert(`⚠️ ${message}`);
  }

  /**
   * Shows error message to user
   * @param message Error message to display
   */
  private showError(message: string): void {
    alert(`❌ ${message}`);
  }

  /**
   * Cleans up resources and closes modals
   */
  private cleanup(): void {
    if (this.progressModal) {
      this.progressModal.close();
      this.progressModal = null;
    }
  }
}

// Export singleton instance for convenience
export const downloadService = new DownloadService();