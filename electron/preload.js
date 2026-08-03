const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  isElectron: true,
  downloadPdf: (url, formData, fileName) =>
    ipcRenderer.invoke('download:pdf', { url, formData, fileName }),
  downloadFile: (url, fileName) =>
    ipcRenderer.invoke('download:file', { url, fileName }),
});
