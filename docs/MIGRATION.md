# Server migration runbook

How to rebuild the whole stack on a new machine (new IP or new host). The Ansible
playbook reproduces all software and configuration; this runbook covers the
**stateful data** the playbook does not manage and the manual steps around it.

## What the playbook reproduces automatically

`common`, `security` (UFW + fail2ban + SSH hardening), `docker`, `monitoring`
(Prometheus + Grafana + node_exporter, dashboards, Telegram + service-down alerts),
`logging` (Loki + Alloy log aggregation), `nginx` reverse proxy, native
`xray` / 3x-ui, and the `sandstorm` game server (steamcmd + configs + mods).

## What you must carry over manually

| Item | Why | How |
|------|-----|-----|
| `x-ui.db` | VPN inbounds, users, Reality keys, panel login | `backup-state.sh` -> `xray_db_restore_path` |
| Let's Encrypt certs | nginx TLS won't start without them | `backup-state.sh` -> `nginx_letsencrypt_restore_path` (or re-run certbot) |
| DNS record | `example.duckdns.org` must point to the new IP | manual, in the DuckDNS panel |
| Vault password | needed to decrypt secrets | keep `~/.ansible_vault_pass` backed up off-repo |

Not carried (acceptable to lose): Prometheus metric history, Grafana volume
(dashboards/alerts are re-provisioned), game saves/stats.

## Procedure

### 1. On the OLD server - back up state
```bash
sudo scripts/backup-state.sh /tmp/infra-state
# copy it to the Ansible control node:
scp -r OLD_HOST:/tmp/infra-state ~/infra-state
```

### 2. On the NEW server - bootstrap access
Fresh Ubuntu, then create the management user the playbook expects:
```bash
sudo adduser --gecos "" ansible && sudo usermod -aG sudo ansible
echo 'ansible ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/ansible
# from the control node:
ssh-copy-id -i ~/.ssh/id_ed25519.pub ansible@NEW_IP
```

### 3. Point inventory and DNS at the new host
- `inventory/hosts.yml` -> set the `production` host `ansible_host` to `NEW_IP`.
- Update `example.duckdns.org` to `NEW_IP` in the DuckDNS panel (manual).

### 4. Run the playbook against production
```bash
cd ansible
ansible-playbook site.yml -e target_hosts=production \
  -e xray_db_restore_path=~/infra-state/x-ui.db \
  -e nginx_letsencrypt_restore_path=~/infra-state/letsencrypt.tgz
```
The xray role restores `x-ui.db` only if no DB exists yet (one-time), and nginx
restores the certs only if they are missing. Re-runs are safe and idempotent.

> No cert backup? After DNS points at the new IP and port 80 is reachable, obtain
> fresh certs with certbot instead, then re-run the playbook.

### 5. Verify
```bash
ssh NEW: 'systemctl is-active x-ui nginx sandstorm-server'
ssh NEW: 'sudo docker ps'                       # grafana, prometheus, node_exporter Up
curl -I https://example.duckdns.org:<grafana-https-port>/   # Grafana via nginx
```
Check the 3x-ui panel for your inbounds, and confirm a Telegram test alert fires.

## Verifying the playbook matches the current prod (before trusting it)

Run a read-only diff against the live server - it changes nothing:
```bash
ansible-playbook site.yml -e target_hosts=production --check --diff
```
