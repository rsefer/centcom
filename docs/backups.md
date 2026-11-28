# Backups

- `scripts/backup.sh` backs up persistent data for the host's configured Compose services to a timestamped directory under `~/centcom-backups/`, prunes backup directories at least 100 days old based on their modification time, and syncs each new backup to S3. Manual runs do not reduce the retention period. `scripts/setup-cron.sh` installs a daily 3:15 AM cron job in each host's local time; logs go to `~/centcom/backup.log`. Deployments ensure the jobs are installed. Run one manually with `make backup` on the host. Missing required data or failed exports make the backup fail without uploading or pruning older backups; check the log for failures. A failed S3 sync also prevents local pruning.
- A failed backup emails `DNS_ALERT_TO`. A successful run writes `~/centcom-backups/.last-success`, which `alert-watch.py` uses to flag backups more than 48 hours old. See [Alerts](resilience.md#alerts).
- Included: the `opengym_data`, `pihole_data`, `pihole_dnsmasq`, `beszel_data`, `beszel_agent_data`, and `caddy_data` volumes; FreshRSS `data/` and `extensions/`; GoLink `/opt/golink/data`; and `.env` (stored as `env`, mode 600).
- **openGym uploaded media (progress photos in `uploads/`) is excluded** to keep backups small. Everything else — accounts, passkeys, workouts, server secrets — is included. `opengym-api` is stopped briefly during its archive for a consistent snapshot and restarted automatically.
- **Pi-hole query history and the compiled blocklist are excluded** (together they are ~290MB of regenerable data). Config is fully covered: `pihole.toml`, `dnsmasq.conf`, `hosts/`, and Pi-hole's own rolling `config_backups/` and `gravity_backups/` (config-only gravity snapshots) are included. After restoring `pihole_data`, recover the adlist/group config with `docker exec pihole cp /etc/pihole/gravity_backups/gravity.db.1 /etc/pihole/gravity.db` and rebuild the blocklist with `docker exec pihole pihole -g`.
- **FreshRSS feed items are excluded** (`users/*/db.sqlite`, `cache/`, `favicons/`). Before archiving, the script exports each user's subscriptions as OPML (`data/opml-<user>.xml`), which carries feeds, categories, and per-feed settings; `config.php` files (including login credentials) are included as-is. After restoring `freshrss_data` and starting FreshRSS, run `sh scripts/restore-freshrss-subscriptions.sh` to initialize the empty SQLite databases and import the OPML. It refuses to modify non-empty databases. Read/starred state is not preserved. Run `make freshrss-categories` afterwards to reapply the duplicate-title category rules (a reload also does this).
- **Beszel historical metrics are excluded**: the `system_stats` and `container_stats` tables are deleted from a pruned copy of the PocketBase database before archiving. Monitor definitions, users, alert config, and the hub's agent SSH key (`id_ed25519`) are kept, so a restored hub reconnects to agents immediately and history simply regrows. The hub is stopped briefly during its archive.
- Archives are created inside throwaway `alpine` containers. S3 sync uses `amazon/aws-cli:2.27.49` with the credentials already in `.env`; newer images may require a newer ARM CPU than the Pi 3. Nothing extra needs installing on the Pi.
- Configuration in `.env`: `BACKUP_S3_BUCKET` (bucket name; S3 sync is skipped when unset), and optionally `BACKUP_S3_PREFIX` (default `centcom`), `BACKUP_ROOT`, `BACKUP_RETENTION` (local retention in days, default 100).
- Provision the S3 side with CloudFormation: `make backup-infra BUCKET=your-bucket IAM_USER=your-route53-user` (run with **admin** AWS credentials, e.g. `AWS_PROFILE=admin`). The stack (`scripts/backup-infra.yaml`) creates a private, encrypted bucket with a lifecycle rule expiring backups after 100 days (override with `DAYS=n`), plus an IAM managed policy attached to your existing Route53 user granting `s3:PutObject`/`s3:GetObject`/`s3:ListBucket` on the `centcom/` prefix. The bucket is retained if the stack is ever deleted. Re-run to update.
- Local pruning does not delete from S3; the stack's lifecycle rule caps storage cost instead.
- Restore a single archive with `./scripts/restore.sh <archive.tar.gz>` — it maps the archive name back to its volume or host path and replaces the target's contents after confirmation. The archive is checked and extracted into a temporary Docker volume before the target is touched; the staging volume needs enough free disk space. A failure during the subsequent replacement can still leave the target incomplete. Stop the owning service first (e.g. `docker compose stop opengym-api`), then `docker compose up -d` afterwards. Test a restore occasionally; a backup you've never restored is a hope, not a backup.

## Vaultwarden encrypted backup

`scripts/backup-vaultwarden.sh` is a second, separate backup of the password
vault, run daily at 4:30 from cron on the host that runs Vaultwarden (southcom).
`make deploy` installs the job; it does nothing until you set a key.

Why it exists next to `backup.sh`: that script skips its S3 upload when any part of
its run fails (a Pi-hole error would also stop the vault upload), and its archives
are not encrypted. This job snapshots the live database with SQLite's `.backup`
(no downtime, and checked with `PRAGMA integrity_check`), adds the RSA keys,
attachments, sends and config, and encrypts the lot with [age](https://age-encryption.org)
to your **public** key. Nothing readable is stored, locally or in S3, and only the
holder of the private key can open it. `icon_cache/` and `tmp/` are left out.

**Setup (once)**
1. On your workstation, install age (`brew install age`) and run
   `age-keygen -o centcom-vault-key.txt`. It prints `Public key: age1...`.
2. Keep `centcom-vault-key.txt` (the private key) somewhere that does not depend on
   Vaultwarden, such as a printed copy or a second password manager. A key that
   lives only in the vault it protects cannot recover it.
3. Add the public key to southcom's `.env`: `VAULTWARDEN_AGE_RECIPIENT=age1...`.
   Several keys, separated by commas, let more than one person or key decrypt.
4. Run `make backup-vaultwarden` on southcom once, and try a restore as below.

Files go to `~/centcom-backups/vaultwarden/` (newest 30 kept; change with
`VAULTWARDEN_BACKUP_KEEP`) and, when `BACKUP_S3_BUCKET` is set, to
`s3://<bucket>/<prefix>/<host>/vaultwarden/`, where the bucket's usual 100-day
expiry applies. A failure emails `DNS_ALERT_TO`, and `alert-watch.py` emails if
the last success is over 48 hours old. For a copy outside AWS, fetch the newest
file to a machine you control, for example
`scp 'southcom.local:centcom-backups/vaultwarden/*.age' ~/vault-backups/`.

**Restore** (on a machine with the private key and Docker):
```sh
age -d -i centcom-vault-key.txt vaultwarden-southcom-<timestamp>.tar.gz.age > vaultwarden_data.tar.gz
docker compose stop vaultwarden
./scripts/restore.sh vaultwarden_data.tar.gz     # replaces the vault volume's contents
docker compose start vaultwarden
```
The file must be named `vaultwarden_data.tar.gz` for `restore.sh` to find the volume.

