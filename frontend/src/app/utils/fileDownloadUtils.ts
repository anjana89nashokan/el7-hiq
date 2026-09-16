import { downloadService } from './downloadService';
import { createProgressModal, createLoadingModal, createDownloadSelectionModal } from './downloadModals';

export { prepareDownloadData, fetchDownloadData, type DownloadResponse, type ApiResponse } from '../end-points/downloadApi';
export { downloadService, DownloadService } from './downloadService';
export { createProgressModal, createLoadingModal, createDownloadSelectionModal } from './downloadModals';
export { extractFileName, downloadBlob, createTimestampedFileName } from './fileUtils';

export const downloadFilesIndividually = async (urls: string[], _folderName: string) => {
  return downloadService.downloadIndividually(urls);
};

export const downloadFilesAsZip = async (urls: string[]) => {
  return downloadService.downloadAsZip(urls);
};

export const showProgressModal = () => {
  return createProgressModal();
};

export const showLoadingModal = () => {
  return createLoadingModal();
};

export const showDownloadSelectionModal = (downloadData: any): Promise<string[]> => {
  return new Promise((resolve) => {
    createDownloadSelectionModal(
      downloadData,
      (urls) => resolve(urls),
      async (urls) => {
        await downloadService.downloadAsZip(urls);
        resolve([]);
      }
    );
  });
};
