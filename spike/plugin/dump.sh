#!/bin/sh
# Spike only: append everything the host gives us to a log inside the plugin state dir.
out="${HERDR_PLUGIN_STATE_DIR:-/tmp}/dump.log"
mkdir -p "$(dirname "$out")"
{
  echo "=== $1 $(date +%H:%M:%S) pid=$$ pwd=$(pwd)"
  env | grep '^HERDR_' | sort
} >> "$out"
