#!/bin/sh
set -e

# Required env: REPO_URL, COMMAND_SECRET
if [ -z "${REPO_URL}" ]; then
  echo "REPO_URL is required"
  exit 1
fi
if [ -z "${COMMAND_SECRET}" ]; then
  echo "COMMAND_SECRET is required"
  exit 1
fi

exec python /app/agent.py
