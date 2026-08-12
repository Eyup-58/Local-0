#!/bin/sh
set -e

# Required env: REPO_URL, and one of COMMAND_SECRET / COMMAND_SECRET_FILE
if [ -z "${REPO_URL}" ]; then
  echo "REPO_URL is required"
  exit 1
fi
if [ -z "${COMMAND_SECRET}" ] && [ -z "${COMMAND_SECRET_FILE}" ]; then
  echo "COMMAND_SECRET or COMMAND_SECRET_FILE is required"
  exit 1
fi

exec python /app/agent.py
