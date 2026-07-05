# Server Infrastructure as Code

[![CI](https://github.com/rkarpovets/server-infrastructure/actions/workflows/ci.yml/badge.svg)](https://github.com/rkarpovets/server-infrastructure/actions/workflows/ci.yml)

Ansible automation that takes a clean Ubuntu host and provisions a complete,
production-grade Linux server: base hardening, Docker, a full **observability
stack** (metrics **and** logs with Telegram alerting), an Nginx reverse proxy
with TLS, an Xray (3x-ui) VPN panel, and a dedicated **Insurgency: Sandstorm**
game server - all described as code and fully **idempotent**.

The repository is the single source of truth for the *configuration* of the
server: one `site.yml` run provisions the production host from scratch, which
makes disaster recovery and migration to new hardware a repeatable, idempotent
operation.

> Hostnames, IPs and the live firewall port map are intentionally **not** in the
> repo: the production IP, TLS domain and non-standard ports all live encrypted
> in Ansible Vault, so a public clone never reveals the live host.

## Highlights

- **Eight focused roles**, one responsibility each, composed by a single `site.yml`.
- **Idempotent by design** - a second run reports `changed=0`; version-pinned,
  heavy installs (steamcmd, 3x-ui) only act when state actually differs, so
  re-runs never disturb live services.
- **Observability as code** - Prometheus, Grafana, Loki, node_exporter and Alloy,
  with datasources, dashboards, alert rules, contact points and notification
  policies all provisioned from files (no click-ops).
- **Secrets in Ansible Vault** - the repo shows *which* secrets exist, never their
  values; the vault password lives outside the repo.
- **Rebuildable from scratch** - one `site.yml` run reproduces the whole host
  on new hardware; host specifics live in `group_vars` and vault, not the roles.
- **Disaster-recovery aware** - stateful data (VPN DB, TLS certs) is backed up
  out-of-band and restored through role variables; see [docs/MIGRATION.md](docs/MIGRATION.md).

## Architecture

```mermaid
flowchart LR
  admin([Admin]):::ext -->|HTTPS| nginx
  players([Players]):::ext -->|UDP game / query| sandstorm

  subgraph host[Linux server]
    nginx[nginx<br/>reverse proxy + TLS] --> grafana

    subgraph obs[Observability stack - Docker]
      grafana[Grafana] --> prometheus[Prometheus]
      grafana --> loki[Loki]
      prometheus --> nodeexp[node_exporter]
      alloy[Grafana Alloy] --> loki
    end

    sandstorm[Insurgency: Sandstorm<br/>steamcmd + manager]
    sandstorm -->|players-online metric| nodeexp
    sandstorm -->|game log| alloy
    journald[systemd journal] --> alloy
    xray[Xray / 3x-ui<br/>VPN panel]
  end

  grafana -->|alerts| tg([Telegram]):::ext
  classDef ext fill:#1f2937,stroke:#475569,color:#e5e7eb;
```

## Roles

| Role | What it does |
|------|--------------|
| `common`     | Base packages, timezone, apt hygiene |
| `security`   | UFW firewall, fail2ban (systemd backend), SSH hardening drop-in |
| `docker`     | Docker CE + Compose plugin from Docker's official apt repository |
| `monitoring` | node_exporter + Prometheus + Grafana via Compose; datasources, dashboards, alert rules, Telegram contact point and notification policy - all provisioned as code |
| `logging`    | Loki + Grafana Alloy via Compose; tails the game log and the systemd journal, 7-day retention, queryable in the same Grafana |
| `nginx`      | Reverse proxy to Grafana, websocket upgrade, security headers, optional TLS (Let's Encrypt restore) |
| `xray`       | Native 3x-ui install (pinned binary + systemd), one-time DB restore |
| `sandstorm`  | steamcmd dedicated server, game configs, mod.io mods, a Python manager, and systemd units |

Applied in dependency order by `site.yml`: `common -> docker -> security ->
monitoring -> logging -> nginx -> xray -> sandstorm`.

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
│       │   └── vault.yml          # ansible-vault encrypted secrets
│       └── production.yml         # production overrides (ports/domain via vault)
└── roles/                         # one responsibility per role
docs/MIGRATION.md                  # disaster-recovery / new-host runbook
scripts/backup-state.sh            # backs up stateful data the playbook does not manage
```

## Configuration and host specifics

Everything host-specific - IPs, ports, TLS domain, firewall rules - is expressed
through `group_vars` and Ansible Vault, never by editing roles. The roles stay
generic, so pointing the playbook at a new host is only an inventory and vault
change - which is what makes migrating to new hardware a low-risk, repeatable
operation.

## Secrets & Vault

All secrets live in `inventory/group_vars/all/vault.yml`, encrypted with
**Ansible Vault** (AES-256). Plaintext aliases in `all/main.yml` and
`production.yml` reference the encrypted values, so the repo documents *which*
secrets exist without ever exposing them:

- Grafana admin password
- Telegram bot tokens (a monitoring bot and a dedicated game bot) + chat id
- Sandstorm RCON password, GSLT, and game-stats tokens
- mod.io API token
- Production host: IP, TLS domain, and non-standard SSH / panel / VPN ports

The vault password is kept outside the repository. The encrypted `vault.yml`
**is** committed - that is what makes a clean clone able to rebuild production.

## Observability

A single Grafana fronts both pillars:

**Metrics** - `node_exporter` (incl. a systemd unit-state textfile collector) is
scraped by Prometheus and visualised in Grafana. Provisioned dashboards:

- *Node Exporter Full* - host vitals.
- *Insurgency: Sandstorm* - a custom dashboard correlating **players online**
  against the **busiest CPU core**. The game server is single-threaded, so
  average CPU hides saturation; the busiest-core view is what actually predicts
  gameplay impact.

**Logs** - Grafana Alloy tails the game log and the systemd journal and ships
them to Loki (7-day retention, enforced by the compactor). Player names are kept
in the log line but never as a label, to keep Loki's index cardinality bounded.

**Alerting** - Grafana alert rules (provisioned as code) page a Telegram channel:
busiest CPU core > 90% for 10 min, RAM > 90%, disk > 85%, and any monitored
systemd service going inactive.

## Security

- **UFW** default-deny with an explicit allow-list per role.
- **fail2ban** (systemd backend) with SSH and nginx jails.
- **SSH hardening** drop-in; key-only by default (production keeps password auth
  as a deliberate, fail2ban-covered fallback on a non-standard port).
- **RCON is never exposed** - it binds loopback and is reached over an SSH
  tunnel; the firewall never opens its port.

## Usage

```bash
cd ansible

# Provision production (the only host, and the default target)
ansible-playbook site.yml

# Dry run - shows the diff, changes nothing
ansible-playbook site.yml --check --diff

# Re-apply just one slice (tagged roles)
ansible-playbook site.yml --tags monitoring,logging
```

Connection details live in the inventory and vault: the production IP
(`vault_production_ip`), SSH port and key path are set in `hosts.yml`, so you
only need the vault password to decrypt them at run time.

## Disaster recovery

The repo is the source of truth for *configuration* only. Stateful data - the
VPN database, TLS certificates, game saves, metric history - is **not** in the
repo. `scripts/backup-state.sh` captures the non-reproducible pieces (the 3x-ui
database and Let's Encrypt certificates) so they can be restored on a fresh host
through role variables. The full new-host runbook is in
[docs/MIGRATION.md](docs/MIGRATION.md).

## Design principles

- **Idempotent** - declare desired state, converge to it, re-run safely.
- **Pinned versions** - reproducibility over freshness for every image and binary.
- **One responsibility per role**, composed rather than monolithic.
- **Secrets and host specifics out of the repo** - vault for values,
  `group_vars` for per-host configuration.
