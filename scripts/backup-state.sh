#!/bin/bash
# Back up the stateful data the playbook does NOT manage, so it can be restored
# on a new host during migration. Run on the CURRENT server (as root), then copy
# the output directory off-box to the Ansible control node.
#
#   sudo ./backup-state.sh [output_dir]
#
# Restore on the new host by pointing the playbook at the files:
#   xray_db_restore_path:          <output_dir>/x-ui.db
#   nginx_letsencrypt_restore_path: <output_dir>/letsencrypt.tgz
set -euo pipefail

OUT="${1:-/tmp/infra-state-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"

# 3x-ui database: VPN inbounds, users, Reality keys, panel credentials.
if [ -f /etc/x-ui/x-ui.db ]; then
    cp -a /etc/x-ui/x-ui.db "$OUT/x-ui.db"
    echo "  + x-ui.db"
fi

# TLS certificates (Let's Encrypt).
if [ -d /etc/letsencrypt ]; then
    tar -C /etc -czf "$OUT/letsencrypt.tgz" letsencrypt
    echo "  + letsencrypt.tgz"
fi

echo "State backed up to: $OUT"
ls -la "$OUT"
