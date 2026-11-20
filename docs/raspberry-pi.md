# Raspberry Pi 4: from a new board to SSH access

How to prepare a clean Raspberry Pi OS for the Energie collector, step by step. At the end the Pi runs
in the network without a monitor or keyboard (headless), the PC reaches it over SSH with a key, and
[deploy/README.md](../deploy/README.md) takes over: `make deploy` installs the rest.

Allow about an hour the first time, most of it waiting for downloads and the first boot.

## What you need

| Part | Notes |
|---|---|
| Raspberry Pi 4 Model B | any RAM size; the collector needs less than 100 MB |
| Power supply 5 V / 3 A, USB-C | the official 15 W supply; weak supplies cause random errors on a Pi 4 |
| microSD card, 16–32 GB | Lite needs 8 GB; prefer an "endurance" card (it is written every minute) |
| SD card reader for the PC | built in or USB |
| Network cable | recommended next to the meters; Wi-Fi works too |
| Case with a heatsink | passive cooling is enough |
| Optional: USB stick or SSD | for the database later (`collector_db`), easier on the SD card |

## 1. SSH key in WSL (on the PC)

Ansible runs in WSL, so the key that logs in to the Pi lives there. In PowerShell:

```sh
wsl
ls ~/.ssh/id_ed25519.pub || ssh-keygen -t ed25519 -C "energie-deploy"
cat ~/.ssh/id_ed25519.pub
```

`ssh-keygen` asks for a passphrase; one is recommended (enter it once per session with
`eval $(ssh-agent) && ssh-add`). The printed line `ssh-ed25519 AAAA… energie-deploy` is the public key
for step 3. Imager can also read it from `\\wsl$\Ubuntu\home\<your user>\.ssh\id_ed25519.pub`.

## 2. Raspberry Pi Imager (on the PC)

```sh
winget install RaspberryPiFoundation.RaspberryPiImager
```

or the installer from <https://www.raspberrypi.com/software/>.

## 3. Write the SD card

Put the card into the reader and start Raspberry Pi Imager.

First, under **App Options** (bottom left), switch off *Enable anonymous statistics telemetry*: the
project sends nothing to third parties.

| Imager tab | Choice |
|---|---|
| **Device** | Raspberry Pi 4 |
| **OS** | *Raspberry Pi OS (other)* → **Raspberry Pi OS Lite (64-bit)** (no desktop) |
| **Storage** | the SD card — check the size; keep *Exclude system drives* on |

**Customisation** (do not skip it — without it the headless Pi cannot be reached):

| Subtab | Setting |
|---|---|
| Hostname | `energy-pi` |
| Localisation | your capital city: sets the time zone (the same as the PC: the collector stores local time), the keyboard and the Wi-Fi country |
| User | a user name (e.g. `pi`) and a strong password: `sudo` asks for it, and so does `make deploy` |
| Wi-Fi | only if there is no cable next to the meters: network name and password |
| Remote Access | **Enable SSH**, *Use public key authentication*, paste the key from step 1 |
| Raspberry Pi Connect | off (it is a cloud service; SSH is enough) |

Check the summary, **Write**, confirm *I understand, erase and write*, and let Imager verify the card
(a few minutes).

## 4. First boot

1. Card into the Pi (label facing away from the board), network cable in, power last.
2. Wait 3–5 minutes: the first boot enlarges the file system, applies the settings and may restart
   once. The green LED flickers while it works.
3. Find the Pi's address in the router (FRITZ!Box: *Home Network → Network*, device `energy-pi`) and
   give it a **fixed IP**: *always assign this network device the same IPv4 address*. From Windows,
   `ping energy-pi.local` also shows it.

Use this IP address from now on, in SSH and in `deploy/inventory.yml`: WSL usually cannot resolve
`.local` names (they use mDNS, which does not reach into WSL's virtual network).

## 5. Log in and check

From WSL, with the fixed address (e.g. `192.168.178.50`):

```sh
ssh pi@192.168.178.50          # first time: answer "yes" to the host key
```

On the Pi:

```sh
cat /etc/os-release | head -1  # Raspberry Pi OS (Debian 12 "bookworm" or newer)
python3 --version              # 3.11 or newer
timedatectl | grep "Time zone" # the same as the PC
vcgencmd get_throttled         # throttled=0x0 — the power supply is fine
df -h /                        # the whole card is in use
exit
```

`throttled` other than `0x0` means under-voltage: use the official power supply before going on.

## 6. Install the collector

Continue with [deploy/README.md](../deploy/README.md) from step 2 (Ansible in WSL, inventory,
`make deploy`). The playbook updates the system, sets up the collector as a service and the nightly
backup.

## If something does not work

| Problem | Remedy |
|---|---|
| No `energy-pi` in the router after 5 minutes | power supply and cable; check the card is fully inserted; write it again with Imager |
| `ssh: Permission denied (publickey)` | the key in Imager was not the one from WSL; write the card again, or log in with a monitor and keyboard and add the key to `~/.ssh/authorized_keys` |
| `REMOTE HOST IDENTIFICATION HAS CHANGED` after rewriting the card | expected: `ssh-keygen -R 192.168.178.50`, then connect again |
| `ping energy-pi.local` fails in WSL | expected; use the IP address |
| Wi-Fi does not connect | the Wi-Fi country (Localisation) must match; the Pi 4 does 2.4 and 5 GHz |

---

© 2026 Taras Bulubas. All rights reserved. See [LICENSE](../LICENSE).
