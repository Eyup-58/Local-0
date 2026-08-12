"""Self-check for the trust boundary: signature verification, payload validation, id state.

Run with `python agent/test_agent.py` (no pytest needed; the image has none).
"""

import hashlib
import hmac
import tempfile
from pathlib import Path

import agent

SECRET = b'test-secret'


def sign(payload: bytes, secret: bytes = SECRET) -> str:
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def test_verify_accepts_matching_signature():
    payload = b'{"id": 1}'
    assert agent.verify(payload, sign(payload), SECRET)


def test_verify_rejects_tampered_payload():
    signature = sign(b'{"id": 1}')
    assert not agent.verify(b'{"id": 2}', signature, SECRET)


def test_verify_rejects_wrong_secret():
    payload = b'{"id": 1}'
    assert not agent.verify(payload, sign(payload, b'other'), SECRET)


def test_verify_rejects_non_hex_signature_without_raising():
    assert not agent.verify(b'{"id": 1}', 'imza-değil', SECRET)
    assert not agent.verify(b'{"id": 1}', '', SECRET)


def test_validate_accepts_argv_batch():
    batch = {'id': 3, 'commands': [{'argv': ['echo', 'hi']}]}
    assert agent.validate(batch, frozenset()) is batch


def test_validate_rejects_command_outside_allowlist():
    batch = {'id': 3, 'commands': [{'argv': ['echo', 'ok']}, {'argv': ['rm', '-rf', '/tmp/x']}]}
    try:
        agent.validate(batch, frozenset({'echo'}))
    except ValueError:
        return
    raise AssertionError('rm should have been rejected')


def test_validate_rejects_string_command():
    try:
        agent.validate({'id': 1, 'commands': [{'cmd': 'rm -rf /'}]}, frozenset())
    except ValueError:
        return
    raise AssertionError('a cmd string is not an argv list')


def test_validate_rejects_missing_or_non_integer_id():
    for bad in ({'commands': []}, {'id': '4', 'commands': []}, {'id': True, 'commands': []}):
        try:
            agent.validate(bad, frozenset())
        except ValueError:
            continue
        raise AssertionError(f'{bad!r} should have been rejected')


def test_read_last_id_defaults_to_zero_on_missing_or_garbage_state():
    with tempfile.TemporaryDirectory() as tmp:
        original = agent.STATE_FILE
        try:
            agent.STATE_FILE = Path(tmp) / 'last_id'
            assert agent.read_last_id() == 0

            agent.STATE_FILE.write_text('bozuk')
            assert agent.read_last_id() == 0

            agent.STATE_FILE.write_text('7\n')
            assert agent.read_last_id() == 7
            # The replay gate: a rolled-back batch carries an id we have already passed.
            assert 3 <= agent.read_last_id()
        finally:
            agent.STATE_FILE = original


if __name__ == '__main__':
    for name, case in sorted(globals().items()):
        if name.startswith('test_'):
            case()
    print('ok')
