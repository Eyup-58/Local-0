"""The key: how it is held in memory, and where it is kept between runs.

docs/SECURITY.md section 7 says secrets come from the environment or the Windows Credential
Manager, never from source, never from a config file, never from a file next to the binary. Section
11 adds the one path M4 introduces - a key typed into the UI - and is explicit that this is a path
*into* Credential Manager rather than a fourth storage location.

The tests below are mostly about the boring leak, because the boring leak is the one that happens:
nobody writes ``print(api_key)``. What happens is that a key ends up inside a dict, a dataclass, an
exception, or a log line, and the interpolation that exposes it was written by somebody who did not
know a secret was in there. So the wrapper is tested against every way Python has of turning an
object into text, not just against ``print``.
"""

from __future__ import annotations

import json
import logging

import pytest

from local_zero_brain.credentials import REDACTED, CredentialStore, Secret

#: Deliberately not shaped like a vendor-issued key. The pre-commit hook matches real prefixes
#: (`AIza…`, `sk-…`, `ghp_…`) and it is right to: a fixture that has to be allowlisted teaches the
#: eye to skip that marker in exactly the files where it matters. What these tests need is a
#: distinctive string, not a plausible one.
VALUE = "local-zero-test-value-0000000000000000"

#: Namespaced so a test run cannot collide with the real entry the product uses.
TEST_TARGET = "LocalZero/test/egress-key"


def test_the_value_is_available_when_it_is_asked_for_explicitly() -> None:
    """The wrapper is not encryption. It makes exposure deliberate, and that is all."""
    assert Secret(VALUE).reveal() == VALUE


def test_repr_does_not_carry_the_value() -> None:
    assert VALUE not in repr(Secret(VALUE))
    assert repr(Secret(VALUE)) == REDACTED


def test_str_does_not_carry_the_value() -> None:
    assert str(Secret(VALUE)) == REDACTED


def test_an_f_string_does_not_carry_the_value() -> None:
    """``__format__`` is a separate hole from ``__str__``.

    An f-string calls format(), and object.__format__ with an empty spec falls back to str() - so
    this passes for free only as long as __str__ is the one overridden. It is asserted separately
    because a later refactor could change that without anybody noticing.
    """
    assert VALUE not in f"key={Secret(VALUE)}"


def test_percent_interpolation_does_not_carry_the_value() -> None:
    assert VALUE not in "key=%s" % (Secret(VALUE),)


def test_a_log_line_does_not_carry_the_value(caplog: pytest.LogCaptureFixture) -> None:
    """The realistic leak. Logging defers formatting, so the wrapper has to survive that too."""
    with caplog.at_level(logging.INFO):
        logging.getLogger("test").info("configured with %s", Secret(VALUE))

    assert VALUE not in caplog.text


def test_a_secret_inside_a_container_does_not_carry_the_value() -> None:
    """Containers print their items with repr(), which is how a secret escapes inside a dict."""
    assert VALUE not in str({"api_key": Secret(VALUE)})
    assert VALUE not in repr([Secret(VALUE)])


def test_a_secret_is_not_json_serializable() -> None:
    """Refusing beats redacting here.

    A Secret that quietly serialized as "[redacted]" would produce a request body that looks valid
    and is not, and the bug would surface as an authentication failure somewhere else entirely.
    """
    with pytest.raises(TypeError):
        json.dumps({"api_key": Secret(VALUE)})


def test_an_empty_secret_is_refused() -> None:
    """An empty key is a missing key wearing the shape of a present one."""
    with pytest.raises(ValueError):
        Secret("")


class TestCredentialManager:
    """Against the real Credential Manager. There is no fake worth trusting here.

    A mock would assert that this code calls the API the way this code calls it, which is the one
    thing that cannot be wrong. What is actually worth proving is that a value written by this
    process comes back byte-identical - the failure mode being the UTF-16 blob encoding, which a
    mock would have cheerfully agreed with.
    """

    @pytest.fixture(autouse=True)
    def cleanup(self) -> None:
        store = CredentialStore(target=TEST_TARGET)
        yield
        store.delete()

    def test_a_stored_key_reads_back_unchanged(self) -> None:
        store = CredentialStore(target=TEST_TARGET)

        store.write(Secret(VALUE))

        assert store.read().reveal() == VALUE

    def test_an_absent_key_reads_as_none_rather_than_raising(self) -> None:
        """A fresh install has no key, and that is an ordinary state rather than an error."""
        store = CredentialStore(target=TEST_TARGET)
        store.delete()

        assert store.read() is None

    def test_has_key_does_not_reveal_it(self) -> None:
        """What the UI is told: whether a key exists, never the key."""
        store = CredentialStore(target=TEST_TARGET)
        store.write(Secret(VALUE))

        assert store.has_key() is True

    def test_deleting_an_absent_key_is_not_an_error(self) -> None:
        store = CredentialStore(target=TEST_TARGET)
        store.delete()

        store.delete()

        assert store.has_key() is False

    def test_overwriting_replaces_rather_than_appends(self) -> None:
        store = CredentialStore(target=TEST_TARGET)
        store.write(Secret(VALUE))

        store.write(Secret("second-value"))

        assert store.read().reveal() == "second-value"
