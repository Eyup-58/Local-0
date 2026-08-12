"""Pull signed command batches from a git repo and run them.

The HMAC signature over commands.json is the trust boundary: nothing runs unless it was
signed with COMMAND_SECRET. The monotonic "id" inside the signed payload is what stops a
batch from re-running every poll, and stops anyone with repo write access (but no secret)
from rolling the repo back to an older signed batch and replaying it.
"""

import hashlib
import hmac
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

REPO_DIR = Path('/app/repo')
COMMANDS_FILE = REPO_DIR / 'commands.json'
SIG_FILE = REPO_DIR / 'commands.json.sig'
STATE_FILE = Path('/app/state/last_id')

POLL_INTERVAL = int(os.environ.get('POLL_INTERVAL', '30'))
COMMAND_TIMEOUT = int(os.environ.get('COMMAND_TIMEOUT', '300'))
GIT_TIMEOUT = int(os.environ.get('GIT_TIMEOUT', '120'))
ALLOWED_COMMANDS = frozenset(
    name for name in os.environ.get('ALLOWED_COMMANDS', '').split(',') if name
)

MAX_LOGGED_OUTPUT = 2000

log = logging.getLogger('agent')


def read_secret() -> bytes:
    """COMMAND_SECRET_FILE first: that is what Docker secrets actually hand you.

    An env var is readable via `docker inspect` and /proc/<pid>/environ.
    """
    path = os.environ.get('COMMAND_SECRET_FILE')
    if path:
        return Path(path).read_bytes().strip()
    return os.environ['COMMAND_SECRET'].encode()


def verify(payload: bytes, signature: str, secret: bytes) -> bool:
    try:
        given = bytes.fromhex(signature)
    except ValueError:
        return False
    expected = hmac.new(secret, payload, hashlib.sha256).digest()
    return hmac.compare_digest(expected, given)


def sync_repo() -> None:
    if not (REPO_DIR / '.git').is_dir():
        # A clone that died halfway leaves a directory behind; without this every later
        # poll would fetch into a non-repo and fail forever.
        shutil.rmtree(REPO_DIR, ignore_errors=True)
        REPO_DIR.parent.mkdir(parents=True, exist_ok=True)
        _git('clone', '--depth', '1', os.environ['REPO_URL'], str(REPO_DIR))
        return
    _git('-C', str(REPO_DIR), 'fetch', '--depth', '1', 'origin')
    # reset rather than pull: idempotent, and does not wedge on a force-push or divergence.
    _git('-C', str(REPO_DIR), 'reset', '--hard', 'FETCH_HEAD')


def _git(*args: str) -> None:
    subprocess.run(['git', *args], check=True, timeout=GIT_TIMEOUT, capture_output=True)


def validate(data: object, allowed: frozenset = ALLOWED_COMMANDS) -> dict:
    """All-or-nothing: one bad entry rejects the whole batch, so nothing runs half-applied."""
    if not isinstance(data, dict):
        raise ValueError('payload must be an object')
    if not isinstance(data.get('id'), int) or isinstance(data.get('id'), bool):
        raise ValueError('id must be an integer')
    commands = data.get('commands')
    if not isinstance(commands, list):
        raise ValueError('commands must be a list')
    for item in commands:
        argv = item.get('argv') if isinstance(item, dict) else None
        if not isinstance(argv, list) or not argv:
            raise ValueError(f'each command needs a non-empty argv list, got {item!r}')
        if not all(isinstance(arg, str) for arg in argv):
            raise ValueError(f'argv entries must be strings, got {argv!r}')
        if allowed and argv[0] not in allowed:
            raise ValueError(f'{argv[0]!r} is not in ALLOWED_COMMANDS')
    return data


def read_last_id() -> int:
    try:
        return int(STATE_FILE.read_text().strip())
    except (OSError, ValueError):
        return 0


def run(argv: list) -> bool:
    log.info('running %s', argv)
    try:
        # No shell: the argv list is the whole reason ALLOWED_COMMANDS can be trusted.
        result = subprocess.run(argv, capture_output=True, text=True, timeout=COMMAND_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.error('command failed: %s', exc)
        return False
    # ponytail: full output is buffered in memory; COMMAND_TIMEOUT bounds it in practice.
    # Switch to streaming if a command ever produces output large enough to matter.
    log.info(
        'exit=%s stdout=%s stderr=%s',
        result.returncode,
        result.stdout[-MAX_LOGGED_OUTPUT:],
        result.stderr[-MAX_LOGGED_OUTPUT:],
    )
    return result.returncode == 0


def process(secret: bytes) -> None:
    if not (COMMANDS_FILE.exists() and SIG_FILE.exists()):
        return
    raw = COMMANDS_FILE.read_bytes()
    if not verify(raw, SIG_FILE.read_text().strip(), secret):
        log.warning('signature missing or invalid; skipping')
        return

    batch = validate(json.loads(raw))
    last_id = read_last_id()
    if batch['id'] <= last_id:
        return

    # Recorded before running, not after: at-most-once. If the container dies mid-batch the
    # batch is skipped rather than replayed, which is the safer default for a command runner.
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(str(batch['id']))
    log.info('applying batch id=%s (%d commands)', batch['id'], len(batch['commands']))

    for item in batch['commands']:
        if not run(item['argv']):
            log.error('batch id=%s aborted after a failing command', batch['id'])
            break


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    secret = read_secret()
    while True:
        try:
            sync_repo()
            process(secret)
        except Exception as exc:
            log.error('poll failed: %s', exc)
        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
