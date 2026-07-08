# Server migration runbook

How to rebuild the whole stack on a new machine (new IP or new host). The Ansible
playbook reproduces all software and configuration; this runbook covers the
**stateful data** the playbook does not manage and the manual steps around it.

## What the playbook reproduces automatically

`common`, `security` (UFW + fail2ban + SSH hardening), `host_tuning` (CPU-core
isolation for the game), `k3s` (the container runtime for everything below),
`monitoring` (Prometheus + Grafana + node_exporter, dashboards, Telegram +
service-down alerts), `logging` (Loki + Alloy log aggregation), `nginx` reverse
proxy, native `xray` / 3x-ui, and the `sandstorm` game server (steamcmd +
configs + mods + the game pod with its RCON-manager sidecar).

> **Fresh host needs ONE reboot** after the first playbook run: the CPU
> isolation (`isolcpus` in GRUB, written by `host_tuning`) only takes effect on
> boot. Everything runs fine before the reboot - the game core just is not
> isolated yet. Verify after reboot: `cat /sys/devices/system/cpu/isolated`.

The playbook runs from a **control node** (your workstation / WSL, or the CI
runner) and pushes over SSH. The target server never holds the playbook or the
vault password - it is a dumb target.

## What you must carry over manually

| Item | Why | How |
|------|-----|-----|
| `x-ui.db` | VPN inbounds, users, Reality keys, panel login | `backup-state.sh` -> `xray_db_restore_path` |
| Let's Encrypt certs | nginx TLS won't start without them | `backup-state.sh` -> `nginx_letsencrypt_restore_path` (or re-run certbot) |
| DNS record | the domain (`vault_nginx_server_name`) must point at the new IP | manual, at your DNS provider |
| Vault password | needed to decrypt secrets | keep `~/.ansible_vault_pass` backed up off-repo |

Not carried (acceptable to lose): Prometheus metric history, Grafana volume
(dashboards/alerts are re-provisioned), game saves/stats.

## Procedure

### 1. On the OLD server - back up state

`backup-state.sh` lives in this repo on the control node, not on the server
(Ansible is push-based; the target never has the playbook). So copy the script
over, run it, then pull the result back:
```bash
# from the control node - adjust port / user / IP to your inventory:
scp -P <ssh_port> scripts/backup-state.sh <user>@<OLD_IP>:/tmp/backup-state.sh
ssh -p <ssh_port> <user>@<OLD_IP> 'sudo bash /tmp/backup-state.sh /tmp/infra-state'
scp -r -P <ssh_port> <user>@<OLD_IP>:/tmp/infra-state ~/infra-state
```
Also copy `~/.ansible_vault_pass` somewhere safe and off-repo (a password manager).

### 2. On the NEW server - bootstrap access

Fresh Ubuntu. The inventory connects as `ansible_user` (currently `ubuntu`),
key-based. Ensure that user has passwordless sudo and your public key:
```bash
# on the new host (as any sudo-capable user):
echo 'ubuntu ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/ubuntu
# from the control node:
ssh-copy-id -i ~/.ssh/id_ed25519.pub ubuntu@<NEW_IP>
```
The `security` role later moves SSH to the non-standard vaulted port and hardens
it - so the very first run connects on the default port 22 (see step 4).

### 3. Point the inventory and DNS at the new host

The production IP and domain live in the vault, not in plaintext files, so a
migration is a vault edit - `hosts.yml` needs no change (it already reads
`ansible_host: "{{ vault_production_ip }}"`):
```bash
ansible-vault edit inventory/group_vars/all/vault.yml
#   set vault_production_ip to <NEW_IP>
```
Then repoint the domain (`vault_nginx_server_name`) at `<NEW_IP>` with your
DNS provider.

### 4. Run the playbook against the new host

`production` is the default target. Pass the backup paths so the one-time state
restore happens:
```bash
cd ansible
ansible-playbook site.yml \
  -e xray_db_restore_path=~/infra-state/x-ui.db \
  -e nginx_letsencrypt_restore_path=~/infra-state/letsencrypt.tgz
```
The xray role restores `x-ui.db` only if no DB exists yet (one-time), and nginx
restores the certs only if they are missing. Re-runs are safe and idempotent.

> **First run on a fresh host:** SSH is still on port 22, but the inventory
> expects the vaulted port. Override it once: add `-e ansible_port=22`. After the
> `security` role moves SSH to the vaulted port, drop the override.

> **No cert backup?** After DNS points at the new IP and port 80 is reachable,
> obtain fresh certs with certbot instead, then re-run the playbook.

> **New host = new SSH host key.** The CI **Deploy** workflow pins it for you
> (`ssh-keyscan` into `known_hosts`, with host key checking on), so a stale or
> spoofed key can't silently redirect the apply. To get the same guarantee on a
> manual run (local `ansible.cfg` keeps `host_key_checking = False`), pin the key
> once first - using the port the host is actually on (22 on a fresh host, the
> vaulted port after the `security` role runs):
> ```bash
> ssh-keyscan -H -p <port> <NEW_IP> >> ~/.ssh/known_hosts
> ```

### 5. Verify
```bash
ssh -p <ssh_port> ubuntu@<NEW_IP> 'systemctl is-active x-ui k3s'
ssh -p <ssh_port> ubuntu@<NEW_IP> 'kubectl get pods -A'
# expect Running: nginx, grafana, prometheus, node-exporter, loki, alloy
# (monitoring ns) and the sandstorm pod 2/2 (game + manager sidecar)
```
Open your Grafana domain over HTTPS, check the 3x-ui panel for your inbounds,
confirm a Telegram test alert fires, and check the server appears in the
in-game browser (or query it: A2S on the query port).

## Verifying the playbook matches the current prod (before trusting it)

Run a read-only check against the live server - it changes nothing. This is also
what the scheduled CD workflow does every day, so a green run is your standing
confidence that a migration will reproduce the server faithfully:
```bash
ansible-playbook site.yml --check --diff
```
