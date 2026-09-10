Set oWS = WScript.CreateObject("WScript.Shell")
sLinkFile = "C:\Users\jr04b\Desktop\Launch AI System.lnk"
Set oLink = oWS.CreateShortcut(sLinkFile)
oLink.TargetPath = "C:\Users\jr04b\ai-system\start-ai.bat"
oLink.WorkingDirectory = "C:\Users\jr04b\ai-system"
oLink.Description = "Launch Self-Hosted AI System"
oLink.IconLocation = "C:\Windows\System32\shell32.dll,16"
oLink.Save
