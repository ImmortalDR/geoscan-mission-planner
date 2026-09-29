#!/usr/bin/env bash
# Infrastructure bootstrap: PostgreSQL+PostGIS, nginx, project venv, MovingAI MAPF datasets.
set -x
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq postgresql-16 postgresql-16-postgis-3 postgresql-client-16 nginx fonts-liberation
python3 -m venv /root/h3/.venv
/root/h3/.venv/bin/pip install -q --upgrade pip wheel
/root/h3/.venv/bin/pip install -q -r /root/h3/requirements.txt
mkdir -p /root/h3/datasets/movingai
cd /root/h3/datasets/movingai
for f in mapf-map.zip mapf-scen-random.zip mapf-scen-even.zip; do
  [ -s "$f" ] || curl -fsSL -o "$f" "https://movingai.com/benchmarks/mapf/$f"
done
ls -la
