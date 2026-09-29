#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
install_dir=/opt/h2-fixture-viewer

id h2-viewer >/dev/null 2>&1 || useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin h2-viewer
install -d -o root -g root -m 0755 "$install_dir/viewer/static" "$install_dir/fixtures"
install -d -o root -g root -m 0755 "$install_dir/src" "$install_dir/artifacts"
install -d -o h2-viewer -g h2-viewer -m 0755 "$repo_dir/artifacts/viewer-runs"
install -o root -g root -m 0644 "$repo_dir/viewer/server.py" "$install_dir/viewer/server.py"
cp -a "$repo_dir/viewer/static/." "$install_dir/viewer/static/"
cp -a "$repo_dir/src/." "$install_dir/src/"
find "$install_dir/fixtures" -type f -delete
install -o root -g root -m 0644 "$repo_dir"/fixtures/*.json "$install_dir/fixtures/"
if [[ ! -x "$install_dir/.venv/bin/python" ]]; then
  python3 -m venv "$install_dir/.venv"
fi
"$install_dir/.venv/bin/pip" install -r "$repo_dir/requirements-tested.txt"
install -o root -g root -m 0644 "$repo_dir/deploy/h2-fixture-viewer.service" /etc/systemd/system/h2-fixture-viewer.service
install -o root -g root -m 0644 "$repo_dir/deploy/nginx-h2-fixture-viewer.conf" /etc/nginx/sites-available/h2-fixture-viewer
ln -sfn /etc/nginx/sites-available/h2-fixture-viewer /etc/nginx/sites-enabled/h2-fixture-viewer
systemctl daemon-reload
systemctl enable h2-fixture-viewer.service
systemctl restart h2-fixture-viewer.service
nginx -t
systemctl reload nginx
curl --fail --silent http://127.0.0.1:8090/health/ready
echo
