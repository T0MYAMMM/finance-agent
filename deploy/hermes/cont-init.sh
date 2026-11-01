#!/command/with-contenv sh
# shellcheck shell=sh
# Boot hook for the finance skill, installed as /etc/cont-init.d/50-finance-agent.
#
# Runs as root on every container start, after Hermes's own 01-/02- hooks have
# chowned and seeded $HERMES_HOME. Everything here is idempotent, so a redeploy
# picks up new code without manual edits on the volume.
set -eu

home="${HERMES_HOME:-/opt/data}"
agent="${FINANCE_AGENT_DIR:-/opt/finance-agent}"

# The CLI's own state (aliases, outbox, notify state) lives on the volume.
install -d -o hermes -g hermes -m 0700 "$home/doeedd" "$home/scripts"

# Hermes cron only runs scripts that are real files inside $HERMES_HOME/scripts
# (symlinks out of it are refused), so copy rather than link.
install -o hermes -g hermes -m 0700 \
  "$agent/deploy/hermes/doeedd_notify.py" "$home/scripts/doeedd_notify.py"

# Tool subprocesses may run with HOME=$HERMES_HOME/home and without HERMES_* variables
# (Hermes strips them from terminal children). Point the fallbacks the CLI and the
# google-workspace scripts use (~/.hermes, ~/finance-system) at the real locations, so
# state, exports and the Google token resolve to one place whichever HOME is in effect.
install -d -o hermes -g hermes -m 0700 "$home/home"
for base in "$home" "$home/home"; do
  ln -sfn "$agent" "$base/finance-system"
done
ln -sfn "$home" "$home/home/.hermes"
