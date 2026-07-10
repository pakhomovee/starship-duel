#!/usr/bin/env bash
# Minimal inbound firewall for the Starship Duel host. Run as root.
# Deny everything inbound except SSH (rate-limited) and HTTP/HTTPS. The app port
# (8000) is NOT opened: it is loopback-only and reached solely via Caddy.
set -euo pipefail

ufw default deny incoming
ufw default allow outgoing

ufw limit 22/tcp   comment 'SSH (rate-limited against brute force)'
ufw allow 80/tcp   comment 'HTTP - ACME challenge + redirect to HTTPS'
ufw allow 443/tcp  comment 'HTTPS'

ufw --force enable
ufw status verbose

echo
echo "NOTE: for extra safety, restrict SSH to your own IP, e.g.:"
echo "  ufw delete limit 22/tcp && ufw allow from <YOUR_IP> to any port 22 proto tcp"
