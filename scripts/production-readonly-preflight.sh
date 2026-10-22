#!/usr/bin/env bash
# Execute through already-authorized SSH. Never changes services, data, or AWS.
set -euo pipefail
expected=i-03328ee0e60347254
actual=$(cat /sys/devices/virtual/dmi/id/board_asset_tag)
test "$actual" = "$expected" || { echo 'WRONG INSTANCE: STOP'; exit 1; }
printf 'Verified instance: %s\n' "$actual"
uname -m
uptime
free -m
df -h / /var/lib/docker
docker version --format '{{.Server.Version}} {{.Server.Arch}}'
docker compose version
docker compose ls --format json
docker ps -a --format '{{.Names}} {{.Image}} {{.Status}}'
for container in $(docker ps -aq); do
  # Explicit fields only: never emit environment or application/private records.
  docker inspect --format '{{.Name}} image={{.Image}} restart={{.HostConfig.RestartPolicy.Name}} mounts={{json .Mounts}} networks={{json .NetworkSettings.Networks}}' "$container"
done
systemctl list-timers --all --no-pager
systemctl list-unit-files --no-pager | awk 'tolower($0) ~ /tpo|watcher|caddy|backup/'
find /etc/cron.d /etc/cron.daily /etc/cron.hourly -maxdepth 1 -type f -printf '%p\n'
# No docker exec, backup, checkpoint, schema query or config-secret inspection.
# The discovered mounts/service identities bind the subsequent stopped-store audit.
