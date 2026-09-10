const {app,BrowserWindow,ipcMain} = require('electron');
const path=require('path');
let win;
function createWindow(){
  win=new BrowserWindow({width:1180,height:760,minWidth:900,minHeight:620,backgroundColor:'#050814',title:'AI System — Jarvis Companion',webPreferences:{preload:path.join(__dirname,'preload.cjs'),contextIsolation:true,nodeIntegration:false}});
  win.loadFile(path.join(__dirname,'index.html'));
}
ipcMain.handle('config:get',()=>({apiBase:process.env.AI_SYSTEM_URL||'http://127.0.0.1:8000'}));
app.whenReady().then(()=>{createWindow();app.on('activate',()=>{if(BrowserWindow.getAllWindows().length===0)createWindow();});});
app.on('window-all-closed',()=>{if(process.platform!=='darwin')app.quit();});
