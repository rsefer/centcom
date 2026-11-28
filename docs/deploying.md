# Deploying and rolling back

Shipping a commit to every host, checking for drift, and undoing a deploy.

## Deploying

Commit and push changes to GitHub, then run `make deploy` from this checkout.
It discovers host names from the published `docker-compose.<host>.yml` files,
checks every host before changing any, and deploys the same commit to each.
It requires Tailscale DNS, SSH access configured for `<host>.local`, a clean
working tree and active cron on each Pi, and a fast-forwardable `main` branch.
Local uncommitted changes are not deployed, and local commits not yet pushed
cause deployment to stop. If a host fails during deployment after preflight,
earlier hosts may already be updated; fix the problem and run it again.
`make deploy HOST=northcom.local` targets one SSH host; `BRANCH=name` selects
another published branch.
After deploying, the last selected host checks both configured Pi-hole
resolvers and the HTTPS dashboard and service URLs. Python 3 and outbound
tailnet access are required on that host. A failed check fails the deployment
command but does not roll back hosts already updated. HTTP redirects and
authentication challenges count as reachable, not proof of application health.
Run `make check-services` on a configured host to repeat the checks.

`make check-hosts` reports drift without changing anything. It connects to
each host as `make deploy` does and compares the checked-out commit (against
each other and `origin/main`), branch and working-tree cleanliness, the
`compose.yaml` symlink target, each container's Compose config hash against the
checked-out files, anything a `docker compose up -d --dry-run` would still
change (such as a newer image), and registry digests for images shared across
hosts. It also flags services that are missing, stopped, or no longer in the
config. A stale config hash or a pending change means the host needs
`make reload`. Locally built images (Caddy)
are not compared by digest across hosts, and hashes assume the same
`COMPOSE_PROFILES` as the `.env` on the host. The command exits non-zero when
drift is found. `make check-hosts HOST=northcom.local` checks one SSH host, and
skips the symlink expectation like `make deploy`.

### New `.env` keys

Each host's private `.env` is not in git, so a commit that needs a new setting
(or renames one) would otherwise fail on the host mid-deploy. The preflight
compares the target commit's `.env.sample` with the host's current one. Any key
the commit adds, including a rename's new name, must already be a `KEY=` line in
every host's `~/centcom/.env`, and the deploy stops before changing any host if
one is missing. A blank value satisfies it for optional settings. Keys the host
already lacked before this commit, and commented-out samples, are not checked.
`SKIP_ENV_CHECK=1 make deploy` bypasses the check.

When a commit renames a key, add the new name to every host first and keep the
old one until the deploy is verified. Rolling back runs the older code, which
still reads the old name.

## Rolling back

`make rollback` undoes a bad deploy. Each `make deploy` records the commit a host
was running before it moved, in the git ref `refs/centcom/previous-deploy` (a ref,
so the working tree stays clean). Rollback uses the same discovery and preflight
as deploy, checking every host before changing any. It then runs
`git reset --hard` to the earlier commit, reinstalls cron and reloads the stack.

- `make rollback` returns every host to the commit it ran before its last deploy.
  If hosts would return to different commits (for example after a single-host
  deploy), it stops and asks for `TO=<commit>`.
- `make rollback HOST=southcom.local` rolls back one SSH host.
- `make rollback TO=<commit>` rolls all hosts to a specific commit. It must be
  an ancestor of what each host is running. Use `git log` locally to pick one.
- It refuses when a host's tree is dirty, a host is unreachable, the target is
  the current commit, or no previous deploy is recorded (hosts that have not
  deployed since this feature was added).
- A rollback restores configuration and code only. It runs `reload.sh` with
  `RELOAD_SKIP_PULL=1`, so it does not pull images, and containers keep whatever
  image version they already have. Images tagged `latest` are not reverted;
  pin the version in Compose to roll an application back. Data volumes are
  never touched, so restore a backup separately if a release migrated data.
- The hosts end up behind `origin/main`, which `make check-hosts` reports as drift.
  Fix the problem and `make deploy` to roll forward, or revert the bad commit
  and push it. A second rollback stops rather than flipping back.
- Like deploy, it checks DNS and service URLs from the last host afterwards, and
  hosts already rolled back stay that way if a later host fails.
