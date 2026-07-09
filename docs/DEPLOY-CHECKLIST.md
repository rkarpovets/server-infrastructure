# Deploy checklist - new IP / new host

Pre-flight list before deploying to a new server. This is the condensed version;
the full runbook (backups, restore paths, rationale) is in
[MIGRATION.md](MIGRATION.md).

`<ansible_user>` below is whatever `ansible_user` your inventory connects as
(currently `ubuntu`) - substitute your own if it differs.

## A. On the OLD server (while it's still up)

- [ ] Get state from the R2 backup. If the old host is still up, trigger a fresh
      snapshot first: `ssh -p <port> <user>@<OLD_IP> 'sudo systemctl start restic-backup.service'`.
      Then restore `x-ui.db` + certs onto the control node per [BACKUP.md](BACKUP.md)
      (you get `/tmp/restore/var/lib/restic-backup/x-ui.db` and `~/letsencrypt.tgz`).
- [ ] Confirm `~/.ansible_vault_pass` is stored off-repo.

## B. On the NEW server

- [ ] Give `<ansible_user>` passwordless sudo:
      ```bash
      echo '<ansible_user> ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/<ansible_user>
      ```
- [ ] Install the **same public key**:
      ```bash
      ssh-copy-id -i ~/.ssh/id_ed25519.pub <ansible_user>@<NEW_IP>
      ```

## C. Config on the control node

- [ ] Point the vault at the new IP - `hosts.yml` needs no change (it reads
      `vault_production_ip`):
      ```bash
      ansible-vault edit inventory/group_vars/all/vault.yml
      #   set vault_production_ip: <NEW_IP>
      ```
- [ ] Repoint DNS `vault_nginx_server_name` -> `<NEW_IP>` at your DNS provider
      (needed for nginx / TLS).
- [ ] Commit + push the vault change.

## D. Run the deploy

**Via GitHub Actions (production):**

- [ ] Actions -> **Deploy - apply to production** -> Run workflow -> type `deploy`.
- [ ] Host key is pinned automatically (`ssh-keyscan`) - nothing to do by hand.

**Locally from the control node (alternative):**

- [ ] Pin the host key first:
      ```bash
      ssh-keyscan -H -p <port> <NEW_IP> >> ~/.ssh/known_hosts
      ```
- [ ] Run the playbook with the restore paths:
      ```bash
      cd ansible
      ansible-playbook site.yml \
        -e xray_db_restore_path=/tmp/restore/var/lib/restic-backup/x-ui.db \
        -e nginx_letsencrypt_restore_path=~/letsencrypt.tgz
      ```

## ! Main gotcha - SSH port on the FIRST run

A fresh host still has SSH on **22**, but the inventory expects the vaulted port.
So the **first** run needs `-e ansible_port=22`. After the `security` role moves
SSH to the vaulted port, drop the override. (On GitHub Actions, add it as an extra
var in the workflow for that first run.)

## E. Verify after deploy

- [ ] `systemctl is-active x-ui k3s` -> both `active`.
- [ ] `kubectl get pods -A` -> nginx, grafana, prometheus, node-exporter, loki,
      alloy `Running`; `sandstorm` pod `2/2` (game + manager sidecar).
- [ ] Grafana opens over HTTPS, 3x-ui panel shows your inbounds, a Telegram test
      alert fires, the game server answers on its query port.
- [ ] **Fresh host only:** reboot once to activate the CPU isolation, then
      `cat /sys/devices/system/cpu/isolated` shows the game core.
