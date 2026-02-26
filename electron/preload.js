const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  isElectron: true,
  downloadPdf: (url, formData, fileName) =>
    ipcRenderer.invoke('download:pdf', { url, formData, fileName }),
});
