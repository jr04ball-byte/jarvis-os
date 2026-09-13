# Run this once to allow an iPhone on the same local network to reach Jarvis.
# This intentionally opens only TCP 8000 to LocalSubnet while leaving Windows in Public mode.

$ErrorActionPreference = 'Stop'
$ruleName = 'Jarvis iPhone Companion Public LocalSubnet'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', ('"' + $PSCommandPath + '"')
    )
    exit
}

Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule

New-NetFirewallRule `
    -DisplayName $ruleName `
    -Description 'Allows token-authenticated Jarvis iPhone companion access from this private local subnet only.' `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort 8000 `
    -Profile Public `
    -RemoteAddress LocalSubnet | Out-Null

Write-Host 'Jarvis iPhone companion access is enabled on TCP 8000 for LocalSubnet only.' -ForegroundColor Green
Write-Host 'Press Enter to close.'
Read-Host | Out-Null
