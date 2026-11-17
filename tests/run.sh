#!/bin/bash
# Start one HA Visio instance in dev mode: run.sh NAME INGRESS_PORT PEER_PORT FAKE_SUPERVISOR_PORT
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
D=/tmp/visio-$1; mkdir -p $D/haconfig/www
[ -f $D/options.json ] || echo "{\"instance_name\":\"$1\",\"ring_duration\":3,\"log_level\":\"debug\"}" > $D/options.json
cd "$HERE/../visio/app"
VISIO_DEV=1 VISIO_DATA=$D VISIO_HA_CONFIG=$D/haconfig VISIO_PORT=$2 VISIO_PEER_PORT=$3 VISIO_SUPERVISOR_URL=http://127.0.0.1:$4 \
  exec python3 -u . > $D/log.txt 2>&1
