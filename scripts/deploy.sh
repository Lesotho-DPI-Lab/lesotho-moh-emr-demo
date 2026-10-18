#!/usr/bin/env bash
# Deploy from moh-ss to moh-mis:   scripts/deploy.sh [user@host]
# Copies the code, pins moh-ss's internal TLS certificate, builds, runs the
# tests, (re)starts the containers and seeds demo data on first run. Secrets
# stay on the target in .env. Brings back the EMR's X-Road client certificate
# for scripts/register-on-ss.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
target="${1:-administrator@10.255.100.183}"
host_ip="${target#*@}"
dir="child-emr"

# The security server presents its internal TLS certificate on 8443.
ss_cert="$(mktemp)"
openssl s_client -connect 127.0.0.1:8443 </dev/null 2>/dev/null | openssl x509 > "$ss_cert"
echo "moh-ss internal TLS certificate: $(openssl x509 -in "$ss_cert" -noout -subject -enddate | tr '\n' ' ')"

rsync -az --delete --exclude .git --exclude .env --exclude certs --exclude data --exclude deploy-out \
  --exclude '__pycache__' --exclude '*.db' ./ "$target:$dir/"
scp -q "$ss_cert" "$target:$dir/moh-ss-internal.crt"
rm -f "$ss_cert"

ssh "$target" bash -s -- "$dir" "$host_ip" <<'REMOTE'
set -euo pipefail
cd "$1"
mkdir -p data
scripts/init-env.sh
scripts/gen-certs.sh "$2" moh-ss-internal.crt
docker compose build --quiet app
docker compose run --rm -T --no-deps app pytest -q -p no:cacheprovider tests </dev/null
docker compose up -d --remove-orphans
docker compose restart nginx >/dev/null
for i in $(seq 1 30); do
  docker compose exec -T app python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" 2>/dev/null && break
  sleep 2
done
docker compose exec -T app python -m emr.seed
docker compose ps
REMOTE

mkdir -p deploy-out
scp -q "$target:$dir/certs/xroad/client.crt" deploy-out/child-emr-client.crt
echo "EMR X-Road client certificate: deploy-out/child-emr-client.crt"
echo "Next (once): scripts/register-on-ss.sh deploy-out/child-emr-client.crt"
