#!/usr/bin/env bash
# Ubuntu host installation only. This script does not create or modify AWS resources.
set -euo pipefail
if [[ ${EUID} -ne 0 ]]; then
  echo 'Run with sudo after setup.sh and start.sh succeed.' >&2
  exit 1
fi
task_root="$(cd "$(dirname "$0")/.." && pwd -P)"
if [[ ! "$task_root" =~ ^/[A-Za-z0-9_./-]+$ ]]; then
  echo 'Use an absolute project path without spaces or shell/systemd special characters.' >&2
  exit 1
fi
test -f "$task_root/.env"
test -f "$task_root/.secrets/auth.json"
test -f "$task_root/compose.yaml"
test -x /usr/bin/docker
/usr/bin/docker compose version >/dev/null
/usr/bin/docker image inspect campus-deploy-platform:0.1.0 >/dev/null
unit_tmp="$(mktemp --suffix=.service)"
trap 'rm -f -- "$unit_tmp"' EXIT
cat > "$unit_tmp" <<EOF
[Unit]
Description=Campus Deploy control-plane and runtime reconciliation
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target
PartOf=docker.service
StartLimitIntervalSec=0

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$task_root
ExecStart=/usr/bin/docker compose --project-directory $task_root -f $task_root/compose.yaml up -d --no-build --wait
ExecStop=/usr/bin/python3 $task_root/scripts/manage.py stop
TimeoutStartSec=180
TimeoutStopSec=60
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target docker.service
EOF
systemd-analyze verify "$unit_tmp"
if [[ -f /etc/systemd/system/campus-deploy.service ]] && ! grep -q '^Description=Campus Deploy control-plane and runtime reconciliation$' /etc/systemd/system/campus-deploy.service; then
  echo 'An unrelated campus-deploy.service exists; refusing to overwrite it.' >&2
  exit 1
fi
install -o root -g root -m 0644 "$unit_tmp" /etc/systemd/system/campus-deploy.service
systemd-analyze verify /etc/systemd/system/campus-deploy.service
systemctl daemon-reload
systemctl enable --now campus-deploy.service
systemctl --no-pager status campus-deploy.service
echo 'Boot recovery enabled. Preserve Docker data-root, project .env and .secrets on EBS.'
