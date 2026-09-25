#!/bin/sh
# Certbot deploy hook: serve newly renewed certificates without stopping the proxy.
set -eu
/usr/bin/docker exec nginx-nginx-1 nginx -t
/usr/bin/docker exec nginx-nginx-1 nginx -s reload
