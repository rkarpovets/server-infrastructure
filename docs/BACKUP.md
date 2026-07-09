# Backups

The `backup` role takes a daily, off-site, client-side-encrypted copy of the data
on the host that the playbook cannot regenerate from code. Storage is Cloudflare R2
(S3-compatible), accessed through restic's `s3:` backend.

## Scope

| Data | Backed up | Rationale |
|------|-----------|-----------|
| `/etc/x-ui/x-ui.db` | Yes | 3x-ui VPN inbounds, users, Reality keys, panel credentials — not reproducible from code |
| `/etc/letsencrypt` | Yes | TLS certificates — re-issuable, but a backup avoids downtime and Let's Encrypt rate limits |
| Ansible code, vault, Grafana dashboards | No | Version-controlled in this repository |
| Game files, mods, game configs | No | Reproducible via steamcmd, mod.io, and the playbook |
| Prometheus TSDB, Loki logs, Grafana runtime state | No | Observability data, ephemeral by design (Loki retains 7 days) |

Backing up only the non-reproducible state keeps restores fast and the off-site
footprint small (a few MB).

## Security model

- **Client-side encryption.** restic encrypts every snapshot with `RESTIC_PASSWORD`
  before it leaves the host (AES-256). R2 stores only ciphertext; a compromise of the
  R2 account does not expose the VPN database.
- **Least-privilege credentials.** The R2 API token is scoped to the single backup
  bucket with object read/write permission only.
- **Secrets at rest.** The R2 credentials and the restic password are stored in the
  Ansible vault and rendered to `/etc/restic-backup/restic.env` (root-owned, `0600`),
  loaded by the systemd unit via `EnvironmentFile` so they never appear in the process
  list or the journal.
- **Consistent database copy.** The live SQLite database is snapshotted with
  `sqlite3 .backup` rather than a plain copy, to avoid capturing a partial write.

## First-time setup

1. Create a private R2 bucket in the Cloudflare dashboard.
2. Create an R2 API token with **Object Read & Write** permission, scoped to that
   bucket. Record the Access Key ID, the Secret Access Key, and the account S3
   endpoint (`https://<account-id>.r2.cloudflarestorage.com`).
3. Generate a restic repository password (for example, `openssl rand -base64 32`)
   and store it securely. It is required to decrypt every snapshot and cannot be
   recovered.
4. Add the five values to the vault:
   ```bash
   ansible-vault edit ansible/inventory/group_vars/all/vault.yml
   ```
   ```yaml
   vault_backup_restic_password:      "<restic password>"
   vault_backup_s3_endpoint:          "https://<account-id>.r2.cloudflarestorage.com"
   vault_backup_s3_bucket:            "<bucket name>"
   vault_backup_s3_access_key_id:     "<R2 Access Key ID>"
   vault_backup_s3_secret_access_key: "<R2 Secret Access Key>"
   ```
5. Deploy the role:
   ```bash
   ansible-playbook site.yml -e target_hosts=production --tags backup
   ```
6. Verify the first backup rather than waiting for the timer:
   ```bash
   sudo systemctl start restic-backup.service
   sudo journalctl -u restic-backup.service -n 30 --no-pager
   ```
   The first run initialises the repository (`restic init`) and ends with a snapshot
   being saved. The environment sets `AWS_DEFAULT_REGION=auto`, the value R2 expects.

## Restore

Restore is expressed as code: the playbook consumes the recovered files through
`xray_db_restore_path` and `nginx_letsencrypt_restore_path`.

1. Provide the R2 credentials and restic password in the environment — either source
   `/etc/restic-backup/restic.env` on a host that already runs the role, or export
   `RESTIC_REPOSITORY`, `RESTIC_PASSWORD`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
   and `AWS_DEFAULT_REGION=auto`.
2. Restore the latest snapshot:
   ```bash
   restic restore latest --target /tmp/restore
   # /tmp/restore/var/lib/restic-backup/x-ui.db
   # /tmp/restore/etc/letsencrypt/...
   ```
3. The nginx role restores certificates from an archive, so re-pack the recovered
   directory:
   ```bash
   tar -C /tmp/restore/etc -czf ~/letsencrypt.tgz letsencrypt
   ```
4. Run the playbook with both restore paths:
   ```bash
   ansible-playbook site.yml \
     -e xray_db_restore_path=/tmp/restore/var/lib/restic-backup/x-ui.db \
     -e nginx_letsencrypt_restore_path=~/letsencrypt.tgz
   ```

Restores should be tested periodically in a non-production environment. See
[MIGRATION.md](MIGRATION.md) for the full new-host procedure.

## Schedule, retention, and monitoring

- **Schedule.** A systemd timer runs daily at `backup_on_calendar` (default 03:30,
  host time). `Persistent=true` runs a missed backup at the next boot.
- **Retention.** `restic forget --prune` keeps `backup_keep_daily` (7) and
  `backup_keep_weekly` (4) snapshots.
- **Integrity.** `restic check` runs on every backup.
- **Monitoring.** After a successful run the script writes
  `infra_backup_last_success_timestamp_seconds` to the node_exporter textfile
  directory. The Grafana `backup_stale` alert fires if that metric's file has not been
  updated for more than 26 hours, indicating that backups have stopped succeeding.

## Optional hardening

R2 bucket-level Object Lock can make snapshots immutable for a retention period,
preventing a compromised host from deleting its own backups. It requires an adjusted
prune strategy and is disabled by default.
