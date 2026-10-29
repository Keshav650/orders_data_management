# Share Meesho Ledger on your local network

One Windows PC runs the server and stores the database. Team members use a
browser on the same trusted office Wi-Fi or Ethernet network. They do not need
Python, Excel, or a separate database. Everyone sees and edits the same records;
refresh a report to see another person's changes. This version uses one shared
team login with full editing, import, export, and database-backup access.

## 1. Start the team server on the host PC

Double-click **start.bat**. It starts a background server on port **8766**
and opens the browser. The command window displays the team address, username,
and a generated password. Keep those details and share them privately with your
team. The username and password stay the same every time you start or restart the app.
Credentials persist in `data/team-access.json`; do not distribute the
project's data folder or publish this file.

Your current network addresses at setup were:

- http://192.168.1.18:8766
- http://192.168.1.61:8766

The PC has two network addresses. Use the address on the same subnet as your
teammates. The launcher lists current addresses each time; addresses can change.
On the host itself, use http://127.0.0.1:8766. Enter username **team** and the
password shown by the launcher when the browser asks for credentials.

Optional **start.bat --local** runs personal mode on port 8765. Both modes use
the same `data/meesho.sqlite3` database. Your existing imports remain available.

## 2. Allow access through Windows Firewall (once)

On a trusted office network, open Windows **Settings → Network & internet**,
select the active Wi-Fi/Ethernet connection, and set its network profile to
**Private**. Leave unfamiliar/public networks as Public. See Microsoft's
[network settings instructions](https://support.microsoft.com/en-us/windows/experience/connectivity-networking/essential-network-settings-and-tasks-in-windows).

Open **PowerShell as Administrator**, then run:

```powershell
New-NetFirewallRule -Name 'Meesho-Ledger-Team-LAN-8766' -DisplayName 'Meesho Ledger Team (private local network)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8766 -Profile Private -RemoteAddress LocalSubnet
```

Run that command once. Alternatively, run the included
`Enable-Team-Firewall.ps1` from an Administrator PowerShell. It checks for an
existing rule. This allows only the local subnet on the Private profile; keep
Windows Firewall enabled. [Microsoft firewall command documentation](https://learn.microsoft.com/en-us/windows/security/operating-system-security/network-security/windows-firewall/configure-with-command-line).

If your office uses a managed Domain network, ask your IT administrator to
create the equivalent rule for the approved office subnet and network profile.

## 3. Connect from another computer

Open the host's team address (for example **http://192.168.1.18:8766**) in
Chrome, Edge, or another browser. Enter the shared login. Bookmark that address.
Team members can select CSV, ZIP, and XLSX files from their own computers on
**Import files**, preview and commit them, and edit prices/packaging under
**Products & costs**. Uploads are stored in the host database and appear for
everyone when they refresh. No shared Windows folder is needed.

Do not use `localhost` or `127.0.0.1` on a teammate's PC: those refer to their PC.

Keep the host powered on and awake. Closing the startup window does not stop
the server. After restarting Windows, run **start.bat** again. A router
DHCP reservation for the host can keep its address stable.

## Stop, change password, and back up

- Double-click **stop-team.bat** on the host to stop network access.
- To change the shared login, edit `username` and `password` in
  `data/team-access.json`. The password must have at least 8 characters.
  The running server picks up the change on the next request; no restart is
  needed. Share the new login privately. Browsers may prompt for it again.
- Use **Download database backup** regularly. Do not copy an open SQLite file
  while users are editing it; the in-app backup creates a consistent snapshot.
- Keep the live database on the host's local disk, not on a shared network drive.
  This project is currently under OneDrive. For ongoing team use, stop both app
  modes and move the complete project to a non-synced local folder such as
  `C:\MeeshoLedger`, then start it there. Do not run copies against a synced DB.
- Team members should coordinate edits to the same SKU: the last saved update
  wins. Individual accounts, permissions, and per-person audit history are not
  included in this shared-login version.

## If a teammate cannot connect

1. Confirm the host can open http://127.0.0.1:8766 and sign in.
2. Confirm both devices use the same office network, not guest Wi-Fi. Guest
   networks can block connections between devices.
3. Check the current address printed by **start.bat** and the firewall
   rule/profile above. Try the other printed address if the host has two adapters.
4. From the teammate's PowerShell, run `Test-NetConnection 192.168.1.18 -Port 8766`
   using the chosen host IP. `TcpTestSucceeded: True` confirms TCP reachability.
5. Check `data/team-server.log` on the host if the app itself fails.

This mode uses **HTTP**, so passwords and business data are not encrypted in
transit. Use it only on a trusted private LAN. Do not forward port 8766 through
your router or expose this server to the internet. Remote/public access needs a
separate HTTPS/VPN deployment. A second-device connection must be checked on
your actual network after the firewall step.
