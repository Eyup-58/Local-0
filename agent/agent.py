import os
import time
import subprocess
import json
import hmac
import hashlib
from pathlib import Path

REPO_DIR = Path('/app/repo')
COMMANDS_FILE = REPO_DIR / 'commands.json'
POLL_INTERVAL = int(os.environ.get('POLL_INTERVAL', '30'))
SECRET = os.environ.get('COMMAND_SECRET')  # HMAC secret


def git_pull():
    if not REPO_DIR.exists():
        REPO_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.check_call(['git', 'clone', os.environ['REPO_URL'], str(REPO_DIR)])
    else:
        subprocess.check_call(['git', '-C', str(REPO_DIR), 'pull'])


def verify_payload(payload: bytes, signature: str) -> bool:
    if not SECRET:
        return False
    mac = hmac.new(SECRET.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, signature)


def run_command(cmd: str):
    print('Running:', cmd)
    try:
        completed = subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
        print('Out:', completed.stdout)
        print('Err:', completed.stderr)
    except subprocess.CalledProcessError as e:
        print('Command failed:', e.returncode, e.output)


def process_commands():
    if not COMMANDS_FILE.exists():
        return
    with open(COMMANDS_FILE, 'rb') as f:
        raw = f.read()
    try:
        meta_path = COMMANDS_FILE.with_suffix('.sig')
        signature = None
        if meta_path.exists():
            signature = meta_path.read_text().strip()
        if signature is None or not verify_payload(raw, signature):
            print('Signature missing or invalid; skipping')
            return
        data = json.loads(raw)
        for item in data.get('commands', []):
            run_command(item.get('cmd'))
    except Exception as e:
        print('Failed processing commands', e)


def main():
    while True:
        try:
            git_pull()
            process_commands()
        except Exception as e:
            print('Loop error:', e)
        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
