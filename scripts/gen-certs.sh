#!/usr/bin/env bash
# Usage: scripts/gen-certs.sh <host-ip> <moh-ss-internal.crt>
# Creates (once) the web server certificate and the X-Road client certificate
# the EMR presents to moh-ss, and pins moh-ss's internal TLS certificate.
set -euo pipefail
cd "$(dirname "$0")/.."
ip="${1:?host IP}"; ss_cert="${2:?moh-ss internal TLS certificate}"
mkdir -p certs/xroad
if [[ ! -f certs/server.crt ]]; then
  openssl req -x509 -newkey rsa:3072 -sha256 -days 825 -nodes \
    -keyout certs/server.key -out certs/server.crt \
    -subj "/C=LS/O=Ministry of Health (demo)/CN=$ip" \
    -addext "subjectAltName=IP:$ip,DNS:moh-mis" \
    -addext "extendedKeyUsage=serverAuth" 2>/dev/null
  chmod 644 certs/server.key   # read by nginx inside its container
fi
if [[ ! -f certs/xroad/client.crt ]]; then
  # Registered on moh-ss as an internal TLS certificate of CHILD-EMR.
  openssl req -x509 -newkey rsa:3072 -sha256 -days 825 -nodes \
    -keyout certs/xroad/client.key -out certs/xroad/client.crt \
    -subj "/C=LS/O=Ministry of Health (demo)/OU=LS-DEMO GOV MOH/CN=CHILD-EMR" \
    -addext "extendedKeyUsage=clientAuth" 2>/dev/null
  chmod 644 certs/xroad/client.key   # read by the app (uid 10001) inside its container
fi
openssl x509 -in "$ss_cert" -out certs/xroad/moh-ss.crt
echo "pinned moh-ss: $(openssl x509 -in certs/xroad/moh-ss.crt -noout -subject -enddate | tr '\n' ' ')"
echo "X-Road client certificate sha1=$(openssl x509 -in certs/xroad/client.crt -noout -fingerprint -sha1 | cut -d= -f2)"
