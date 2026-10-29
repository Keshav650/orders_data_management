# Run once from an Administrator PowerShell on the host PC.
$ErrorActionPreference = 'Stop'
$ruleName = 'Meesho-Ledger-Team-LAN-8766'
if (-not (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name $ruleName -DisplayName 'Meesho Ledger Team (private local network)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8766 -Profile Private -RemoteAddress LocalSubnet | Out-Null
}
Write-Host 'Port 8766 is allowed only from the local subnet on Private networks.'
