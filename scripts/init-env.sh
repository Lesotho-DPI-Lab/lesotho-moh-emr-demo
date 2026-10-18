#!/usr/bin/env bash
# Create .env with random secrets and initial passwords, once. Prints the
# passwords a single time: hand them to the nurses and the administrator.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -f .env ]]; then echo ".env exists; leaving it alone"; exit 0; fi
rand() { openssl rand -hex "$1"; }
mpho="$(rand 9)"; thato="$(rand 9)"; admin="$(rand 9)"
sed -e "s/^EMR_SECRET_KEY=.*/EMR_SECRET_KEY=$(rand 32)/" \
    -e "s/^EMR_DB_PASSWORD=.*/EMR_DB_PASSWORD=$(rand 24)/" \
    -e "s/^EMR_PASSWORD_NURSE_MPHO=.*/EMR_PASSWORD_NURSE_MPHO=$mpho/" \
    -e "s/^EMR_PASSWORD_NURSE_THATO=.*/EMR_PASSWORD_NURSE_THATO=$thato/" \
    -e "s/^EMR_PASSWORD_EMR_ADMIN=.*/EMR_PASSWORD_EMR_ADMIN=$admin/" \
    .env.example > .env
chmod 600 .env
echo "Created .env. Initial accounts (shown once):"
echo "  nurse.mpho  / $mpho"
echo "  nurse.thato / $thato"
echo "  emr.admin   / $admin"
