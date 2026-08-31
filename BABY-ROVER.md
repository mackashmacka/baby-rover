# baby-rover — system document

Single source of truth for this box. Everything: hardware, OS, network, auth, services,
toolchain, Claude Code config. Captured 2026-08-31 08:50 UTC.

---

## 1. Identity & access

| | |
|---|---|
| Hostname | `baby-rover` |
| Address | `192.168.1.104/24` (DHCP, eth0) · `baby-rover.local` via mDNS/avahi |
| Gateway | `192.168.1.1` (also the DNS server; search domain `Home`) |
| User | `baby-rover` uid 1000, `/bin/bash` |
| Groups | `baby-rover adm dialout sudo video` |
| Sudo | **passwordless** — `/etc/sudoers.d/90-cloud-init-users`: `baby-rover ALL=(ALL) NOPASSWD:ALL` |
| Console | autologin on tty1 (`getty@tty1.service.d/` override, `agetty --autologin`) |
| Machine ID | `2a343bd19e324589bd399058d8ae2e93` |
| Timezone | **UTC** (`Etc/UTC`), NTP active via systemd-timesyncd, clock synced |

```bash
ssh baby-rover      # client-side alias; key auth, no IP to remember
```

Two ed25519 keys authorised in `~/.ssh/authorized_keys`:
`mackashmacka@mackashmacka` and one tagged `20260627`.

---

## 2. Hardware

| | |
|---|---|
| Board | Raspberry Pi 5 Model B Rev 1.1 |
| CPU | 4× Cortex-A76 aarch64, max 2400 MHz |
| RAM | 7.8 GiB (632 MiB used, 6.9 GiB free at capture) |
| Swap | **none** |
| Temp | 46.6 °C idle |
| Throttling | `throttled=0x0` — clean, no undervolt or thermal events |

### Storage — read this before you trust it
Root is **not** on an SD card or SSD. It's on a **SanDisk Cruzer Blade USB flash drive**:

```
sda    29.3G  Cruzer Blade
├─sda1   512M vfat  /boot/firmware   94M used
└─sda2  28.7G ext4  /                2.3G used, 25G free (9%)
```

Cruzer Blades are slow (~20 MB/s write) and have no wear levelling worth the name. With no
swap configured, a memory spike has nowhere to go — but that's the better tradeoff here,
since swapping onto this stick would kill it. Treat the rootfs as disposable: keep nothing
here you can't reimage or re-clone. `fstrim.timer` runs weekly but does nothing useful on
a USB stick.

---

## 3. Network

```
eth0    UP     192.168.1.104/24  metric 100   ← the only live link
wlan0   DOWN                                  ← unconfigured, wpa_supplicant enabled
lo      UNKNOWN 127.0.0.1/8
```

Default route: `192.168.1.1 dev eth0 proto dhcp`. DNS: `192.168.1.1` via systemd-resolved
stub (127.0.0.53). DNSSEC unsupported, DNS-over-TLS off, LLMNR off.

### Open ports
| Port | Proto | What |
|---|---|---|
| 22 | tcp v4+v6 | OpenSSH |
| 53 | tcp/udp localhost only | systemd-resolved stub |
| 5353 | udp v4+v6 | avahi — this is what makes `baby-rover.local` work |
| 68, 546 | udp | DHCP client v4/v6 |

Nothing else listens. **UFW is installed and its unit is enabled, but the firewall itself
is `inactive`** — there are no packet filter rules at all. Fine on a trusted LAN, not fine
if this box ever gets a public interface or port forward.

---

## 4. SSH posture

Effective `sshd -T`:

```
port 22
pubkeyauthentication      yes
passwordauthentication    yes        ← still enabled
kbdinteractiveauthentication no
permitrootlogin           without-password
permitemptypasswords      no
x11forwarding             yes
usepam                    yes
clientaliveinterval       0
```

Two things worth knowing, stated plainly rather than assumed:

1. **`PasswordAuthentication` is still `yes` at the daemon.** In practice you log in by
   key, and the `baby-rover` account has no usable password set — so password login fails
   anyway. But the daemon will still offer and accept the method if a password ever gets
   set on any account. If you want key-only enforced properly:
   ```bash
   echo 'PasswordAuthentication no' | sudo tee /etc/ssh/sshd_config.d/99-keyonly.conf
   sudo systemctl restart ssh
   ```
2. `PermitRootLogin without-password` allows root login by key. No root authorized_keys
   exist right now, so it's inert — set it to `no` if you want it inert by policy too.

---

## 5. Boot & services

```
Startup finished in 5.614s (kernel) + 9.099s (userspace) = 14.714s
graphical.target reached after 8.518s
```

Down from ~61s. Two changes got it there: **snapd purged** (`snap` is gone entirely) and
**`systemd-networkd-wait-online.service` masked** — that unit alone was ~30s of blocking
wait for a link that DHCP had already brought up.

### Masked units
`cryptdisks`, `cryptdisks-early`, `hwclock`, `multipath-tools-boot`, `screen-cleanup`,
`sudo.service`, `systemd-networkd-wait-online`, `x11-common`.

### Failed units
None. `systemctl --failed` is clean.

### Remaining boot cost (top offenders)
```
1.994s dev-sda2.device          ← the USB stick enumerating, unavoidable
1.858s cloud-init-local
1.204s ModemManager
1.123s udisks2
1.096s apport
1.054s rpi-eeprom-update
 936ms pollinate
 813ms cloud-config
```

**Dead weight still enabled** — none of this does anything useful on a headless Pi, and
it's roughly 5–6s of the remaining boot:
`ModemManager` (no cellular modem), `open-vm-tools` + `vgauth` (VMware guest tools, on
bare metal), `multipathd` + `open-iscsi` (no SAN), `pollinate` (one-shot entropy seed,
already done), `apport` (crash reporting), `cloud-init` ×4 (only meaningful on first
boot). Disabling them is the next boot win if you want one.

### Timers
`sysstat-collect` (10min), `logrotate`, `dpkg-db-backup`, `man-db`, `fwupd-refresh`,
`motd-news`, `update-notifier-*`, `systemd-tmpfiles-clean`, `e2scrub_all`, `fstrim`.
No user crontab. `/etc/cron.d/`: `e2scrub_all`, `sysstat`.

### Updates
Unattended-upgrades **on**: `Update-Package-Lists "1"`, `Unattended-Upgrade "1"`.
The box patches itself and may reboot on its own schedule.

---

## 6. Toolchain

| Installed | Version |
|---|---|
| git | 2.43.0 |
| python3 | 3.12.3 |
| curl | 8.5.0 |
| wget | 1.21.4 |
| rsync | 3.2.7 |
| tmux | present |
| vim | 9.1 |
| htop | 3.3.0 |
| jq | 1.7 |
| ufw | 0.36.2 (inactive) |

**Not installed:** `pip3`, `node`, `npm`, `docker`, `gcc`, `make`, `fail2ban`, `snap`.

No compiler and no pip means anything needing a build step or a Python package will fail
until you install `build-essential` / `python3-pip`. Worth knowing before you start
something and hit it mid-way.

`PATH` gets `$HOME/.local/bin` prepended at the end of `~/.bashrc`. That directory holds
exactly one thing: `claude`.

---

## 7. Claude Code

| | |
|---|---|
| Version | 2.1.251 |
| Binary | `~/.local/bin/claude` |
| Config root | `~/.claude/` |
| Global settings | `~/.claude/settings.json` |
| App state | `~/.claude.json` |
| Managed policy | none — `/etc/claude-code/` does not exist |

### Permission mode: bypass, always

Every session on this box opens in **bypassPermissions**. No approval prompts, ever.
`bypass permissions` should show in red at the bottom of the UI on every launch. If it
doesn't, something below got reverted.

**`~/.claude/settings.json`**
```json
{
  "theme": "dark",
  "agentPushNotifEnabled": true,
  "permissions": {
    "defaultMode": "bypassPermissions"
  }
}
```

**`~/.claude.json`** — one added key, pre-accepts the one-time confirmation screen so it
never appears again:
```json
"bypassPermissionsModeAccepted": true
```

Applied 2026-08-31 and **verified live**: a fresh headless session ran an arbitrary shell
command in a directory it had never been trusted for — zero prompts, exit 0.

Two changes rather than one because they do different jobs: `defaultMode` sets the mode
each session starts in; `bypassPermissionsModeAccepted` suppresses the interactive gate
that otherwise stands in front of that mode the first time.

### What can silently undo it
1. **Running as root.** Claude Code refuses bypass mode under uid 0 — never `sudo claude`.
   Passwordless sudo means you never need to.
2. A project-level `.claude/settings.json` with its own `permissions.defaultMode` — project
   settings beat global.
3. `.claude/settings.local.json` in a project — beats both.
4. A managed policy at `/etc/claude-code/managed-settings.json` setting
   `disableBypassPermissionsMode`. None exists; don't create one.
5. `--permission-mode` passed on the CLI, for that run only.
6. **A Claude Code auto-update.** It updates itself; re-verify after a version bump.

### Belt-and-braces (not currently applied)
```bash
claude --dangerously-skip-permissions          # per-run override
alias claude='claude --dangerously-skip-permissions'   # or add to ~/.bashrc
```

### Revert
Pre-change snapshots: `~/.claude/backups/settings.json.pre-bypass.<ts>` and
`claude.json.pre-bypass.<ts>`. Copy back, or strip the keys:
```bash
python3 - <<'PY'
import json
p='/home/baby-rover/.claude/settings.json'; s=json.load(open(p))
s.get('permissions',{}).pop('defaultMode',None); json.dump(s,open(p,'w'),indent=2)
p='/home/baby-rover/.claude.json'; d=json.load(open(p))
d.pop('bypassPermissionsModeAccepted',None); json.dump(d,open(p,'w'),indent=2)
PY
```

### Layout
```
~/.claude/
├── settings.json        # global config — bypass default lives here
├── .credentials.json    # auth token (0600)
├── backups/             # pre-change snapshots
├── projects/-home-baby-rover/memory/   # persistent agent memory
├── plugins/  sessions/  shell-snapshots/  cache/  downloads/
└── history.jsonl
~/.claude.json           # app state, onboarding flags, bypass acceptance
```

---

## 8. Security model — deliberate, not accidental

This box combines: passwordless sudo, tty1 autologin, no firewall, and Claude Code running
in bypass mode by default. Any agent session here can do anything the user can do, which
is everything, without asking.

That is the intended setup — it's a dedicated Pi on a trusted LAN whose rootfs lives on a
disposable USB stick. The tradeoff only holds while those conditions hold. It stops being
reasonable the moment this box gets a port forward, a public IP, a second user, production
credentials, or data that isn't backed up elsewhere. If any of those change, revisit
§4 (SSH), §3 (UFW) and §7 (bypass) together — they're one decision, not three.

---

## 9. Verify after any change or update

```bash
claude --version
python3 -c "import json;print(json.load(open('/home/baby-rover/.claude/settings.json'))['permissions'])"
python3 -c "import json;print(json.load(open('/home/baby-rover/.claude.json')).get('bypassPermissionsModeAccepted'))"
systemctl --failed
systemd-analyze
df -h /            # USB stick — watch it
vcgencmd measure_temp; vcgencmd get_throttled   # expect 0x0
```

Expect `{'defaultMode': 'bypassPermissions'}`, `True`, zero failed units, `0x0` throttling.
Then launch `claude` and confirm the red indicator.
