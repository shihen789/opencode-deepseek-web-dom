const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  lockUuid: (uuid) => ipcRenderer.invoke('lock', uuid),
  unlockUuid: () => ipcRenderer.invoke('unlock'),
  newSession: () => ipcRenderer.invoke('new-session'),
  deleteSession: (uuid) => ipcRenderer.invoke('delete-session', uuid),
  backfillTitles: () => ipcRenderer.invoke('backfill-titles'),
  onStateUpdate: (cb) => ipcRenderer.on('state-update', (e, data) => cb(data)),
  onLogUpdate: (cb) => ipcRenderer.on('log-update', (e, data) => cb(data)),
  onTriggerNewSession: (cb) => ipcRenderer.on('trigger-new-session', () => cb()),
  minimize: () => ipcRenderer.send('window-minimize'),
  close: () => ipcRenderer.send('window-close')
});