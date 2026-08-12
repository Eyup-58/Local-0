# Local Jarvis Agent (Dockerized)

Pulls a signed command batch from a git repo and runs it inside an isolated container.

Files:
- `Dockerfile` — container image
- `entrypoint.sh` — enforces required env vars, then starts the agent
- `agent.py` — main loop: sync repo, verify HMAC, check batch id, run commands
- `commands.example.json` — command file format
- `test_agent.py` — self-check for signature/validation/state (`python test_agent.py`)

## Command file format

```json
{
  "id": 1,
  "commands": [
    {"argv": ["echo", "hello from jarvis"]},
    {"argv": ["date"]}
  ]
}
```

Commands are argv lists, not shell strings — there is no shell, so there is nothing to
inject into and `ALLOWED_COMMANDS` is a real restriction rather than a speed bump.

**`id` must increase on every batch you publish.** The agent records the last id it applied
and refuses anything less than or equal to it. That is what stops a batch from re-running on
every poll, and what stops someone with repo write access (but no secret) from reverting the
repo to an older signed batch and replaying it.

A batch runs at most once: the id is recorded *before* execution, so a container that dies
mid-batch skips the rest rather than repeating it. Commands run in order and stop at the
first failure.

## Signing

```bash
openssl dgst -sha256 -hmac "$COMMAND_SECRET" -hex commands.json \
  | awk '{print $NF}' > commands.json.sig
git add commands.json commands.json.sig && git commit -m "cmd batch 1" && git push
```

Both files must be committed together. The signature covers the raw bytes of
`commands.json`, and is verified before the JSON is parsed.

## Run

```bash
docker build -t jarvis-agent .
docker run -d \
  --name jarvis-agent \
  --restart unless-stopped \
  -v jarvis-state:/app/state \
  -e REPO_URL=https://github.com/yourname/your-private-repo.git \
  -e COMMAND_SECRET_FILE=/run/secrets/command_secret \
  -e POLL_INTERVAL=30 \
  -e COMMAND_TIMEOUT=300 \
  -e ALLOWED_COMMANDS=git,systemctl,docker \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --pids-limit 128 \
  --memory 512m \
  jarvis-agent
```

`-v jarvis-state:/app/state` is not optional. Without it the last-applied id is lost on every
container restart, the counter resets to 0, and previously applied batches become replayable.

`COMMAND_SECRET_FILE` is preferred over `COMMAND_SECRET`: an env var is readable through
`docker inspect` and `/proc/<pid>/environ`. Either works; the file wins if both are set.

`ALLOWED_COMMANDS` is optional (empty = no restriction). It matches `argv[0]` exactly. If any
command in a batch is not allowed, the **entire batch** is rejected — no partial application.

## Security notes

- The HMAC secret is the trust boundary. Anyone holding it can run anything the container
  can run. Store it in Docker secrets or a host secret manager, never in the repo.
- Use a **private** repo. `Eyup-58/Local-0` is currently public: the signature still prevents
  forgery, but every command you publish would be world-readable.
- Command stdout/stderr go to the container log (last 2000 chars each). A command that echoes
  a secret leaks it there.
- Rotate `COMMAND_SECRET` if it is ever exposed, and bump `id` well past the last value.
