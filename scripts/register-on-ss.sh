#!/usr/bin/env bash
# Run on moh-ss:   scripts/register-on-ss.sh deploy-out/child-emr-client.crt
# Configures the CHILD-EMR client subsystem through the security server's REST
# API: connection type HTTPS and the EMR's TLS client certificate as an internal
# certificate. Idempotent.
#
# Uses XROAD_API_KEY when set; otherwise creates a short-lived key with the
# admin credentials (curl prompts for the password) and revokes it at the end.
set -euo pipefail
cert="${1:?EMR client certificate}"
client="${XROAD_CLIENT_ID:-LS-DEMO:GOV:MOH:CHILD-EMR}"
api="https://localhost:4000/api/v1"
admin_user="${XROAD_ADMIN_USER:-administrator}"

key_id=""
if [[ -z "${XROAD_API_KEY:-}" ]]; then
  resp="$(curl -sfk -u "$admin_user" -X POST -H 'Content-Type: application/json' \
          --data '["XROAD_SERVICE_ADMINISTRATOR"]' "$api/api-keys")"
  XROAD_API_KEY="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["key"])' <<<"$resp")"
  key_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$resp")"
  trap 'curl -sfk -u "$admin_user" -X DELETE "$api/api-keys/$key_id" >/dev/null && echo "temporary API key revoked"' EXIT
fi
auth=(-H "Authorization: X-Road-ApiKey token=$XROAD_API_KEY")

info="$(curl -sfk "${auth[@]}" "$api/clients/$client")" || { echo "client $client not found on this security server"; exit 1; }
python3 -c 'import json,sys; c=json.load(sys.stdin); print(f"client {c[\"id\"]}: status {c[\"status\"]}, connection {c[\"connection_type\"]}")' <<<"$info"

fp="$(openssl x509 -in "$cert" -noout -fingerprint -sha1 | cut -d= -f2 | tr -d : | tr A-F a-f)"
if curl -sfk "${auth[@]}" "$api/clients/$client/tls-certificates" | grep -qi "$fp"; then
  echo "TLS certificate $fp already registered"
else
  openssl x509 -in "$cert" -outform DER | curl -sfk "${auth[@]}" -X POST -H 'Content-Type: application/octet-stream' \
    --data-binary @- "$api/clients/$client/tls-certificates" >/dev/null
  echo "TLS certificate $fp registered"
fi

curl -sfk "${auth[@]}" -X PATCH -H 'Content-Type: application/json' \
  --data '{"connection_type":"HTTPS"}' "$api/clients/$client" >/dev/null
echo "connection type set to HTTPS"
