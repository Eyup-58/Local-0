# Local Jarvis Agent (Dockerized)

Purpose: a minimal, secure pattern for pulling signed command files from a private GitHub repo and executing them inside an isolated container.

Files:
- `Dockerfile` — container image
- `entrypoint.sh` — starts the agent and enforces required env vars
- `agent.py` — main loop: git pull, verify HMAC signature, run commands
- `commands.example.json` — example commands format

Usage (build & run):

```bash
docker build -t jarvis-agent .
docker run -d \
  --name jarvis-agent \
  --restart unless-stopped \
  -e REPO_URL=https://github.com/yourname/your-private-repo.git \
  -e COMMAND_SECRET=your_hmac_secret \
  -e POLL_INTERVAL=30 \
  jarvis-agent
```

Security notes:
- Keep `COMMAND_SECRET` secret (use host secret manager or Docker secrets)
- Run container in isolated network and with minimal privileges
- Sign `commands.json` with HMAC SHA256 and place signature in `commands.json.sig`
