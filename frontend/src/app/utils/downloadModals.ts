import type { DownloadResponse } from '../end-points/downloadApi';

export interface ModalControls {
  updateProgress: (current: number, total: number, message: string) => void;
  close: () => void;
}

export const createProgressModal = (): ModalControls => {
  const modal = document.createElement('div');
  modal.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-50';
  
  const content = document.createElement('div');
  content.className = 'bg-white rounded-lg p-8 max-w-md w-full mx-4 text-center';
  
  content.innerHTML = `
    <div class="flex flex-col items-center space-y-4">
      <div class="w-16 h-16 relative">
        <div class="w-16 h-16 border-4 border-gray-200 rounded-full"></div>
        <div class="w-16 h-16 border-4 border-brand-primary rounded-full absolute top-0 left-0 animate-spin" style="border-right-color: transparent; border-top-color: transparent;"></div>
      </div>
      <h3 class="text-lg font-semibold text-gray-900">Preparing Download...</h3>
      <div class="w-full bg-gray-200 rounded-full h-2">
        <div id="progress-bar" class="bg-brand-primary h-2 rounded-full transition-all duration-300" style="width: 0%"></div>
      </div>
      <p id="progress-text" class="text-sm text-gray-600">Initializing...</p>
      <p id="progress-count" class="text-xs text-gray-500">0 / 0</p>
    </div>
  `;
  
  modal.appendChild(content);
  document.body.appendChild(modal);
  
  const progressBar = content.querySelector('#progress-bar') as HTMLElement;
  const progressText = content.querySelector('#progress-text') as HTMLElement;
  const progressCount = content.querySelector('#progress-count') as HTMLElement;
  
  return {
    updateProgress: (current: number, total: number, message: string) => {
      const percentage = Math.round((current / total) * 100);
      if (progressBar) progressBar.style.width = `${percentage}%`;
      if (progressText) progressText.textContent = message;
      if (progressCount) progressCount.textContent = `${current} / ${total}`;
    },
    close: () => {
      if (document.body.contains(modal)) {
        document.body.removeChild(modal);
      }
    }
  };
};

export const createLoadingModal = (): { close: () => void } => {
  const modal = document.createElement('div');
  modal.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-50';
  
  const content = document.createElement('div');
  content.className = 'bg-white rounded-lg p-8 max-w-sm w-full mx-4 text-center';
  
  content.innerHTML = `
    <div class="flex flex-col items-center space-y-4">
      <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-brand-primary"></div>
      <h3 class="text-lg font-semibold text-gray-900">Loading Files...</h3>
      <p class="text-sm text-gray-600">Please wait while we fetch your files</p>
    </div>
  `;
  
  modal.appendChild(content);
  document.body.appendChild(modal);
  
  return {
    close: () => {
      if (document.body.contains(modal)) {
        document.body.removeChild(modal);
      }
    }
  };
};

export const createDownloadSelectionModal = (
  downloadData: DownloadResponse,
  onConfirm: (urls: string[]) => void,
  onZipDownload: (urls: string[]) => void
): void => {
  const modal = document.createElement('div');
  modal.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-50';
  
  const content = document.createElement('div');
  content.className = 'bg-white rounded-lg p-6 max-w-lg w-full mx-4';
  
  const hasFiles = downloadData.files && downloadData.files.length > 0;
  
  if (!hasFiles) {
    content.innerHTML = `
      <h3 class="text-lg font-semibold mb-4">No Downloads Available</h3>
      <p class="text-gray-600 mb-6">No files are available for download.</p>
      <div class="flex justify-end">
        <button id="close-modal" class="px-4 py-2 bg-gray-600 text-white rounded hover:bg-gray-700">
          Close
        </button>
      </div>
    `;
    
    modal.appendChild(content);
    document.body.appendChild(modal);
    
    const closeBtn = content.querySelector('#close-modal');
    closeBtn?.addEventListener('click', () => {
      document.body.removeChild(modal);
    });
    
    return;
  }
  
  content.innerHTML = `
    <h3 class="text-lg font-semibold mb-4">Select Files to Download</h3>
    <div class="space-y-2 mb-6 border rounded p-3">
      <label class="flex items-center space-x-2 p-2 hover:bg-gray-50 rounded">
        <input type="checkbox" checked value="${downloadData.files?.join('|||') || ''}" class="download-checkbox" data-type="files">
        <div class="flex-1">
          <span class="font-medium">Uploaded Files (${downloadData.files?.length || 0} files)</span>
        </div>
      </label>
    </div>
    <div class="flex justify-end space-x-3">
      <button id="cancel-download" class="px-4 py-2 text-gray-600 border border-gray-300 rounded hover:bg-gray-50">
        Cancel
      </button>
      <button id="confirm-download" class="px-4 py-2 bg-brand-primary text-white rounded hover:bg-brand-primary-hover">
        Download Selected
      </button>
      <button id="zip-download" class="px-4 py-2 bg-brand-primary text-white rounded hover:bg-brand-primary-hover">
        Download As Zip
      </button>
    </div>
  `;
  
  modal.appendChild(content);
  document.body.appendChild(modal);
  
  const cancelBtn = content.querySelector('#cancel-download');
  const confirmBtn = content.querySelector('#confirm-download');
  const zipBtn = content.querySelector('#zip-download');
  const checkboxes = content.querySelectorAll('.download-checkbox') as NodeListOf<HTMLInputElement>;
  
  const getSelectedUrls = (): string[] => {
    const selectedUrls: string[] = [];
    checkboxes.forEach((checkbox) => {
      if (checkbox.checked) {
        selectedUrls.push(...checkbox.value.split('|||').filter(url => url.trim() !== ''));
      }
    });
    return selectedUrls;
  };
  
  const closeModal = (): void => {
    if (document.body.contains(modal)) {
      document.body.removeChild(modal);
    }
  };
  
  cancelBtn?.addEventListener('click', closeModal);
  
  confirmBtn?.addEventListener('click', () => {
    const selectedUrls = getSelectedUrls();
    closeModal();
    onConfirm(selectedUrls);
  });
  
  zipBtn?.addEventListener('click', () => {
    const selectedUrls = getSelectedUrls();
    closeModal();
    onZipDownload(selectedUrls);
  });
};