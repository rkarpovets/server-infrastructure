# Server Infrastructure as Code

[![CI](https://github.com/rkarpovets/server-infrastructure/actions/workflows/ci.yml/badge.svg)](https://github.com/rkarpovets/server-infrastructure/actions/workflows/ci.yml) [![CD](https://github.com/rkarpovets/server-infrastructure/actions/workflows/cd.yml/badge.svg)](https://github.com/rkarpovets/server-infrastructure/actions/workflows/cd.yml) [![Terraform](https://github.com/rkarpovets/server-infrastructure/actions/workflows/terraform.yml/badge.svg)](https://github.com/rkarpovets/server-infrastructure/actions/workflows/terraform.yml)

Ansible automation that takes a clean Ubuntu host and provisions a complete,
production-grade Linux server on a single-node **Kubernetes (k3s)** cluster:
base hardening, a full **observability stack** (metrics **and** logs with
Telegram alerting), an Nginx reverse proxy with TLS, an Xray (3x-ui) VPN panel,
and two **Insurgency: Sandstorm** dedicated servers (classic and Hardcore
Checkpoint), each pinned to its own fully **isolated CPU core** - all described
as code and fully **idempotent**.

Ansible stays the single source of truth and the only deploy mechanism: it
renders the Kubernetes manifests from vault-backed variables and applies them
through `kubernetes.core.k8s` with `kubectl apply` semantics, so anything
removed from a template is removed from the cluster too. One `site.yml` run
provisions the production
host from scratch, which makes disaster recovery and migration to new hardware
a repeatable operation.

> Hostnames, IPs and the live firewall port map are intentionally **not** in the
> repo: the production IP, TLS domain and non-standard ports all live encrypted
> in Ansible Vault, so a public clone never reveals the live host.

## Highlights

- **Kubernetes without the ceremony** - k3s single-node with Traefik/ServiceLB
  disabled (the host already owns 80/443), NodePorts bound to loopback only
  (kube-proxy NATs around the host firewall - a lesson this repo encodes),
  manifests templated and applied by Ansible, no Helm, no registry.
- **A quiet core per game server** - `isolcpus`/`nohz_full`/`rcu_nocbs` + IRQ
  steering carve one core per server out of the scheduler entirely; each
  single-threaded game server is pinned to its own core with `taskset`, and the
  hot mod.io SDK threads are evicted back to the general cores by a host-side
  timer.
- **Several servers from one list** - every game server is one entry in
  `sandstorm_instances` (ports, core, mode, mutators, tag, secrets). Install
  dirs, configs, firewall rules, pods, log shipping, metrics and the dashboard
  all follow from that list.
- **Game + RCON manager as one pod** - the Python manager runs as a sidecar
  (no shared process namespace: it talks to the game only over loopback RCON).
  The manager heals what RCON can reach; if the game hangs so hard RCON stops
  answering, the kubelet liveness probe restarts the pod. **Nothing restarts
  the game automatically on config changes** - that is always a deliberate,
  manual rollout in a no-players window.
- **Observability as code** - Prometheus, Grafana, Loki, node_exporter and Alloy
  in-cluster, with datasources, dashboards, alert rules, contact points and
  notification policies all provisioned from files (no click-ops).
- **CI that sees the real YAML** - every PR renders the k8s manifests with dummy
  secrets and validates them with kubeconform, next to yamllint and ansible-lint;
  a scheduled pipeline runs `--check` against production daily and reports drift
  (the `kubernetes.core.k8s` module diffs the live cluster objects in check mode).
- **Secrets in Ansible Vault** - the repo shows *which* secrets exist, never their
  values; the vault password lives outside the repo.
- **Idempotent by design** - a second run reports `changed=0`; version-pinned,
  heavy installs (steamcmd, 3x-ui, k3s) only act when state actually differs.

## Architecture

```mermaid
flowchart LR
  admin([Admin]):::ext -->|HTTPS| nginx
  players([Players]):::ext -->|UDP game / query| game

  subgraph host[Linux server - k3s single node]
    subgraph k8s[k3s workloads]
      nginx[nginx<br/>hostPort 80 / TLS] --> grafana
      grafana[Grafana] --> prometheus[Prometheus]
      grafana --> loki[Loki]
      prometheus --> nodeexp[node_exporter]
      alloy[Grafana Alloy] --> loki

      subgraph pod1[game pod 1 - hostNetwork]
        game1[Sandstorm classic<br/>taskset -> core 3]
        manager1[RCON manager<br/>sidecar] -->|RCON 127.0.0.1| game1
      end
      subgraph pod2[game pod 2 - hostNetwork]
        game2[Sandstorm Hardcore<br/>taskset -> core 2]
        manager2[RCON manager<br/>sidecar] -->|RCON 127.0.0.1| game2
      end
    end

    certbot[certbot<br/>webroot renewals] -.->|certs| nginx
    journald[systemd journal] --> alloy
    board[(shared board state)]
    xray[Xray / 3x-ui VPN<br/>native systemd]
  end

  manager1 & manager2 -->|players metric| nodeexp
  game1 & game2 -->|game log| alloy
  manager1 & manager2 --> board
  grafana -->|alerts| tg([Telegram]):::ext
  board -->|one pinned board| tg
  board -->|status + activity channels| dc([Discord]):::ext
  classDef ext fill:#1f2937,stroke:#475569,color:#e5e7eb;
```

Xray stays native on purpose: it works at the network layer (443, VPN inbounds)
where containerization buys nothing and complicates everything. A mixed runtime
is a decision, not an accident.

## Roles

| Role | What it does |
|------|--------------|
| `common`      | Base packages, timezone, apt hygiene |
| `security`    | UFW firewall, fail2ban (systemd backend), SSH hardening drop-in |
| `host_tuning` | Full CPU-core isolation for the game servers: GRUB (`isolcpus`, `nohz_full`, `rcu_nocbs`) + IRQ affinity steering |
| `k3s`         | k3s install (pinned, Traefik/ServiceLB disabled, loopback-only NodePorts), kubeconfig for the admin user |
| `monitoring`  | node_exporter (DaemonSet) + Prometheus + Grafana; datasources, dashboards, alert rules, Telegram contact point - all rendered into ConfigMaps/Secrets and applied as manifests |
| `logging`     | Loki + Grafana Alloy; tails every server's game log, the RCON managers and the systemd journal, labelled per server, queryable in the same Grafana |
| `nginx`       | nginx Deployment on hostPort 80/TLS: fronts Grafana, serves ACME webroot renewals, keeps file logs for the fail2ban jails |
| `xray`        | Native 3x-ui install (pinned binary + systemd), one-time DB restore |
| `sandstorm`   | For every server in `sandstorm_instances`: steamcmd install, mod.io mods, game configs (with per-server overrides from the game repo), `.env`, firewall rules and a two-container game pod (game + RCON-manager sidecar); plus the log-backup cleanup timer and the shared Telegram board directory |
| `backup`      | Daily restic backup of the non-reproducible state (3x-ui VPN DB, TLS certs) to Cloudflare R2, client-side encrypted; systemd timer + a heartbeat metric that drives a stale-backup alert |

Applied in dependency order by `site.yml`: `common -> security -> host_tuning ->
k3s -> monitoring -> logging -> nginx -> xray -> sandstorm -> backup`.

## Repository layout

```
ansible/
├── ansible.cfg
├── site.yml                       # entry point - the whole stack
├── inventory/
│   ├── hosts.yml                  # inventory hosts
│   └── group_vars/
│       ├── all/
│       │   ├── main.yml           # shared vars + secret aliases
│       │   ├── sandstorm.yml      # the game servers: one entry per server
│       │   └── vault.yml          # ansible-vault encrypted secrets
│       └── production.yml         # production overrides (ports/domain via vault)
└── roles/                         # one responsibility per role
    └── */templates/k8s/           # Kubernetes manifests (Jinja2-templated)
terraform/                         # Cloudflare R2 backup bucket as code
docs/MIGRATION.md                  # disaster-recovery / new-host runbook
docs/DEPLOY-CHECKLIST.md           # pre-flight checklist for a new-IP deploy
scripts/render-k8s-manifests.py    # CI: render manifests with dummy secrets for kubeconform
```

Backups: see [docs/BACKUP.md](docs/BACKUP.md) for the restic/R2 setup and the restore runbook.

## Configuration and host specifics

Everything host-specific - IPs, ports, TLS domain, firewall rules - is expressed
through `group_vars` and Ansible Vault, never by editing roles. Host facts that
cannot be assumed are resolved at deploy time (the game pod runs as the *actual*
uid of the steam user, looked up via `getent` - hardcoding uid 1000 is exactly
the kind of assumption that once crash-looped a cutover).

## Game servers

Every Sandstorm server is one entry in
[`group_vars/all/sandstorm.yml`](ansible/inventory/group_vars/all/sandstorm.yml):

| Field | Meaning |
|-------|---------|
| `name` | Deployment name, `SERVER_ID` and the `server` label on metrics and logs |
| `dir` | Install dir under `steamapps/common`, plus a `~/<dir>` symlink |
| `cpu` | Isolated core the game is pinned to |
| `game_port` / `query_port` / `rcon_port` | Host ports (`hostNetwork`); RCON never opens in the firewall |
| `server_name`, `max_players`, `game_mode`, `mutators` | What players see and play |
| `tag` | Prefix in Telegram and Discord messages and the sort order on the shared board |
| `env_file` | The server's `.env` in the steam home |

Secrets sit next to the list in `sandstorm_instance_secrets`, keyed by server
name - never inside the list itself, because Ansible prints the whole loop item
when a task fails, and these tasks run in CI against a public repo.

Game configs come from the separate game repo
([insurgency-sandstorm-server](https://github.com/rkarpovets/insurgency-sandstorm-server)).
A server reads `servers/<name>/<path>` from it when that file exists, otherwise
the shared file from the repo root - so a second server only carries the files
it actually changes.

`Game.ini` is deployed owned by `root` (mode `0644`): the game writes its
in-memory config back to disk on a clean shutdown, which silently threw away a
`Game.ini` deployed while it was running. Read-only, the file on disk is always
the one from the repo.

### Adding a server

1. Add the entry to `sandstorm.yml` with free ports, a free core and a new tag.
2. Add its GSLT, GameStats token and RCON password to the vault and reference
   them in `sandstorm_instance_secrets`. A GSLT can't be shared between servers.
3. Put the files that differ under `servers/<name>/` in the game repo.
4. If it needs a new core: extend `host_tuning_isolated_cpus` and keep
   `host_tuning_irq_affinity_mask` / `host_tuning_general_cpus` in sync. That
   takes a reboot.
5. Deploy. steamcmd installs the new copy of the game (~5 GB) and the pod
   starts on its own.

### Rolling out changes

1. Merge game repo changes first: every deploy copies configs from its `main`.
2. Merge the infrastructure change; the post-merge drift check must end with
   `failed=0`.
3. Run **Deploy**. Config and `.env` changes never restart a game; only a
   change to a game pod's manifest rolls that pod, so deploy those in a
   no-players window too.
4. In a no-players window, restart only the server that changed:
   `kubectl -n sandstorm rollout restart deployment <name>`.

## Secrets & Vault

All secrets live in `inventory/group_vars/all/vault.yml`, encrypted with
**Ansible Vault** (AES-256). Plaintext aliases in `all/main.yml` and
`production.yml` reference the encrypted values, so the repo documents *which*
secrets exist without ever exposing them:

- Grafana admin password
- Telegram bot tokens (a monitoring bot and a dedicated game bot) + chat id
- Discord webhook URLs for the game board and the join/leave channel
- Sandstorm RCON password, GSLT, and game-stats tokens - one set per server
- mod.io API token
- Production host: IP, TLS domain, and non-standard SSH / panel / VPN ports

The vault password is kept outside the repository. The encrypted `vault.yml`
**is** committed - that is what makes a clean clone able to rebuild production.

## Observability

A single Grafana fronts both pillars:

**Metrics** - `node_exporter` (incl. textfile collectors for systemd unit states
and the game's own metrics) is scraped by Prometheus. Provisioned dashboards:

- *Node Exporter Full* - host vitals.
- *Insurgency: Sandstorm* - the ops view of the game, with a server selector:
  current scenario (map + side), players online, and the load of that server's
  **isolated game core** - the one number that decides tick stability - plus a
  map-history timeline and the live game log.

**Logs** - Grafana Alloy tails every server's game log, the RCON managers and the
systemd journal and ships them to Loki, each game stream labelled with its
`server`. Game logs are kept 3 days; the managers' join/leave events, the only
durable record of who played, are kept a year. Player names are kept in the log
line but never as a label, to keep Loki's index cardinality bounded.

**Alerting** - Grafana alert rules (provisioned as code) page a Telegram channel:
busiest CPU core > 90%, RAM > 90%, disk > 85%, any monitored systemd unit going
inactive, and a stale game-manager heartbeat per server (a sidecar can die while
its game plays on - k8s only restarts the pod when the *game* stops answering
RCON).

**Game board** - a separate game bot keeps one pinned Telegram message with a
section per server (players, map, roster) and posts tagged join/leave messages.
The same board goes to a read-only Discord `#server-status` channel as one embed
card per server, and the join/leave messages to an `#activity` channel, both
through channel webhooks. The managers coordinate through a shared state
directory, so two pods never overwrite each other's edits.

## Security

- **UFW** default-deny with an explicit allow-list per role - plus the hard-won
  caveat that kube-proxy NodePorts are NATed *around* the INPUT chain, so k3s
  binds them to loopback only.
- **fail2ban** (systemd backend) with SSH and nginx jails; the containerized
  nginx keeps writing real files to `/var/log/nginx` for exactly this reason.
- **SSH hardening** drop-in; key-only authentication on a non-standard port.
- **RCON is never exposed** - it is reached over loopback (sidecar) or an SSH
  tunnel; the firewall never opens its port.

## Usage

```bash
cd ansible

# Provision production (the only host, and the default target)
ansible-playbook site.yml

# Dry run - shows what would change, including live-cluster manifest drift
ansible-playbook site.yml --check

# Re-apply just one slice (tagged roles)
ansible-playbook site.yml --tags monitoring,logging
```

Game-affecting note: the sandstorm role never restarts a running game. After
changing game configs or `.env`, apply with a manual
`kubectl -n sandstorm rollout restart deployment <name>` (`sandstorm`,
`sandstorm-hardcore`) in a no-players window - see
[Rolling out changes](#rolling-out-changes).

Manifests are applied with `apply: true`, so a field removed from a template
leaves the cluster on the next deploy. The one exception is Grafana: its
dashboards ConfigMap is larger than the 256 KB `last-applied-configuration`
annotation allows, so it is still merged - a dashboard removed from the repo
has to be deleted from the cluster by hand.

## Disaster recovery

The repo is the source of truth for *configuration* only. Stateful data - the
VPN database, TLS certificates, game saves, metric history - is **not** in the
repo. The `backup` role backs up the non-reproducible pieces (the 3x-ui database
and Let's Encrypt certificates) off-site to Cloudflare R2, client-side encrypted,
so they can be restored on a fresh host through role variables - see
[docs/BACKUP.md](docs/BACKUP.md). The R2 bucket that receives these backups is
itself defined as code in [`terraform/`](terraform/) (Cloudflare provider):
Terraform owns the bucket's lifecycle, while restic and Ansible own the backups
that flow into it. The full new-host runbook is in
[docs/MIGRATION.md](docs/MIGRATION.md); for a quick pre-flight before deploying
to a new IP, use [docs/DEPLOY-CHECKLIST.md](docs/DEPLOY-CHECKLIST.md).

## Design principles

- **Idempotent** - declare desired state, converge to it, re-run safely.
- **Pinned versions** - reproducibility over freshness for every image and binary.
- **One responsibility per role**, composed rather than monolithic.
- **Ansible renders, Kubernetes runs** - manifests are code-reviewed Jinja2,
  applied and drift-checked by the same tool that owns the rest of the host.
- **What the repo says is what runs** - apply semantics prune removed fields,
  and files the game would overwrite are deployed read-only.
- **The live game is sacred** - no automation may restart it; every
  game-affecting action is manual, explicit, and scheduled around players.
- **Secrets and host specifics out of the repo** - vault for values,
  `group_vars` for per-host configuration.
