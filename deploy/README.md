# Deploying the collector to a Raspberry Pi (Ansible)

From a clean SD card to a running collector with two commands on the PC, repeatable after every
change. The playbook does what [collector/install.sh](../collector/install.sh) does by hand, plus the
system around it:

| Role | What it does |
|---|---|
| `base` | `apt` upgrade, daily security updates (`unattended-upgrades`), the PC's time zone, Python 3.11+ check |
| `collector` | user `energie`, `/opt/energie-collector/collector.py`, `collector.toml` from the inventory, the systemd service, a check of `/api/health` |
| `backup` | nightly `sqlite3 .backup`, one file per weekday, optionally `rsync` to the NAS |

Why Ansible and not Terraform: Terraform creates resources through an API (cloud servers); a Pi has
none, its "infrastructure" is an SD card. Ansible configures a running system over SSH, needs nothing
installed on the Pi, and a second run changes only what differs. Kubernetes would be far too heavy for
one standard-library script that needs the GPIO pins.

## 1. The Pi with a clean system (once, by hand)

Follow [docs/raspberry-pi.md](../docs/raspberry-pi.md): an SSH key in WSL, Raspberry Pi OS Lite
(64-bit) written with Raspberry Pi Imager, first boot, a fixed IP address, a first SSH login.

## 2. Ansible on the PC (once)

Ansible does not run on Windows itself; it runs in WSL (Ubuntu):

```sh
wsl
sudo apt update && sudo apt install -y pipx
pipx install --include-deps ansible-core
pipx ensurepath                      # then open a new WSL shell
ansible-playbook --version
```

The SSH key must live in WSL (`~/.ssh`): OpenSSH refuses a key from `/mnt/c/...` because the Windows
drive shows it as readable by everyone.

## 3. Inventory

```sh
copy deploy\inventory.example.yml deploy\inventory.yml
```

Fill in the Pi's fixed IP address, the user and `energie_timezone` (the same as the PC: the collector
stores local time). Use the IP, not `energy-pi.local`: WSL usually cannot resolve `.local` names.
git ignores `inventory.yml` — it holds the addresses of your devices. Every setting and its default
is in `roles/*/defaults/main.yml`.

## 4. Deploy

```sh
make deploy                                  # the whole setup; asks for the Pi user's sudo password
make discover NET=192.168.178.0/24           # Shelly devices, ready to paste under collector_devices
make deploy                                  # again, with the devices
make deploy ARGS="--check --diff"            # only show what would change
```

With a passphrase on the SSH key, load it into an agent first (once per WSL shell):
`eval $(ssh-agent) && ssh-add` — Ansible cannot answer the passphrase prompt itself.

`make deploy` asks for the *BECOME password*: the password of the user created in Imager (Raspberry
Pi OS asks for it on every `sudo`). The last task prints the API address — enter it in the app
(*Settings*, `http://<IP of the Pi>:8600`). Without make (Linux, macOS, or inside WSL):
`cd deploy && ansible-playbook -i inventory.yml site.yml --ask-become-pass`.

## Gas meter

Wire the reed contact between GPIO17 and GND, set `collector_gas_enabled: true` in the inventory,
`make deploy`, then calibrate once to the reading on the counter:

```sh
ssh -t pi@192.168.178.50 sudo -u energie python3 /opt/energie-collector/collector.py calibrate-gas 12345.678
```

## Database and backup

- `collector_db` moves the database to a USB stick or SSD (mount it first, e.g. by its UUID in
  `/etc/fstab`); the SD card copes with a write a minute, a stick lasts longer.
- Every night the backup role writes `collector-Mon.db` … `collector-Sun.db` to
  `/opt/energie-collector/backup/`. With `backup_rsync_target` set, it also copies them to the NAS over
  SSH; the playbook prints the key of the user `energie` to add to `~/.ssh/authorized_keys` there.

## Updating

After a change to `collector/collector.py` or the inventory: `make deploy`. The service restarts only
when its code, `collector.toml` or the unit changed.

---

© 2026 Taras Bulubas. All rights reserved. See [LICENSE](../LICENSE).
