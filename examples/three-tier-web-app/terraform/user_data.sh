#!/bin/bash
# Bootstrap script for application instances.
set -euo pipefail

dnf update -y
dnf install -y nginx

# The load balancer health check targets /health.
mkdir -p /usr/share/nginx/html
echo "ok" > /usr/share/nginx/html/health

systemctl enable --now nginx
