#!/bin/sh
# Copyright (c) 2026 Taras Bulubas. All rights reserved.
# Use requires prior written permission; see LICENSE.

# Install as a systemd service on Raspberry Pi OS / Armbian (Debian 12+, Python 3.11+).
# Run from this folder: sudo sh install.sh
set -e
id energie >/dev/null 2>&1 || useradd --system --home /opt/energie-collector --shell /usr/sbin/nologin energie
getent group gpio >/dev/null && usermod -aG gpio energie || true
mkdir -p /opt/energie-collector
cp collector.py /opt/energie-collector/
if [ -f collector.toml ]; then cp collector.toml /opt/energie-collector/;
elif [ ! -f /opt/energie-collector/collector.toml ]; then cp collector.toml.example /opt/energie-collector/collector.toml; fi
chown -R energie: /opt/energie-collector
cp energie-collector.service /etc/systemd/system/
# without GPIO (Odroid) the group does not exist
getent group gpio >/dev/null || sed -i '/SupplementaryGroups/d' /etc/systemd/system/energie-collector.service
systemctl daemon-reload
systemctl enable --now energie-collector
echo "Done. Edit /opt/energie-collector/collector.toml, then: sudo systemctl restart energie-collector"
echo "Test: curl http://localhost:8600/api/health"
