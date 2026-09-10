const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('aiSystem',{getConfig:()=>ipcRenderer.invoke('config:get')});
