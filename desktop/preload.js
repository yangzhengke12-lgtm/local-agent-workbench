const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('workbench', {
  selectWorkspaceDirectory: () => ipcRenderer.invoke('workspace:select-directory'),
});
