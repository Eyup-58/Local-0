"""One interface over the two modes, and the shared parts neither should reinvent.

docs/SECURITY.md section 11 chose Selectable Hybrid: Local (Ollama, loopback) is the default and
Cloud (Gemini) is opt-in. What that means here is that the *caller* never branches on the mode. It
holds a Provider; which one it holds was decided once, at construction, by ``build_provider``.

Two rules live in this module rather than in either provider:

* **A missing key is a construction failure.** Cloud mode without a key raises before anything is
  asked of it, because "the key is missing" surfacing as an authentication error on the user's first
  question is a strictly worse version of the same message.
* **Structured output retries a fixed number of times and then fails.** A loop that re-prompts until
  the model complies is a loop with no upper bound on cost or time, and the model that produced
  prose twice is not obviously about to stop.

The HTTP call is stdlib ``urllib.request``. It is deliberate rather than incidental: the brain
already runs blocking work on a worker thread (``ws/server.py::_execute``), the egress guard patches
the socket layer beneath every client equally, and one more dependency on this path would be one
more library capable of opening a connection.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any, Literal, Protocol, runtime_checkable

from local_zero_brain.credentials import Secret

ProviderMode = Literal["local", "cloud"]

#: Three attempts, then a clean failure. Named rather than inlined so the test asserting the loop
#: terminates is asserting against the same number the loop uses.
MAX_STRUCTURED_ATTEMPTS = 3

#: Appended on a retry. Short on purpose - a long correction is itself a prompt, and the model that
#: ignored the instruction once is not more likely to follow a longer one.
REPAIR_NOTE = "\n\nReturn only a JSON object. No prose, no code fence."

DEFAULT_TIMEOUT_SECONDS = 60


class ProviderError(RuntimeError):
    """Anything the provider layer could not complete. Never carries a key."""


class MissingKey(ProviderError):
    """Cloud mode was selected and there is no key to use."""


class MalformedOutput(ProviderError):
    """The model's response could not be read as what it was asked for."""


@runtime_checkable
class Provider(Protocol):
    """What the rest of the brain sees. Neither planner nor reader knows which one it holds."""

    name: str

    def complete(self, prompt: str, *, system: str | None = None) -> str: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def build_provider(*, mode: ProviderMode, key: Secret | None, model: str | None = None) -> Provider:
    """The single place a provider is chosen. Fails closed on an unknown mode."""
    if mode == "local":
        from local_zero_brain.llm.ollama import OllamaProvider

        return OllamaProvider(model=model) if model else OllamaProvider()

    if mode == "cloud":
        if key is None:
            raise MissingKey(
                "Cloud mode needs a Gemini key and none is stored. Enter one in the interface - it "
                "is written to the Windows Credential Manager - or switch back to Local mode, which "
                "sends nothing off this machine."
            )

        from local_zero_brain.llm.gemini import GeminiProvider

        return GeminiProvider(key=key, model=model) if model else GeminiProvider(key=key)

    raise ValueError(f"unknown provider mode {mode!r}")


def complete_json(
    provider: Provider,
    prompt: str,
    *,
    system: str | None = None,
    attempts: int = MAX_STRUCTURED_ATTEMPTS,
) -> dict[str, Any]:
    """Asks for a JSON object and insists, up to ``attempts`` times, then gives up."""
    failure = ""

    for attempt in range(attempts):
        raw = provider.complete(prompt if attempt == 0 else prompt + REPAIR_NOTE, system=system)

        try:
            parsed = json.loads(_unfence(raw))
        except json.JSONDecodeError as error:
            failure = str(error)
            continue

        if isinstance(parsed, dict):
            return parsed

        failure = f"the model returned a {type(parsed).__name__} rather than an object"

    raise MalformedOutput(
        f"{provider.name} did not return a JSON object in {attempts} attempts. Last failure: "
        f"{failure}"
    )


#: What each status means to somebody reading the panel, rather than to somebody reading an RFC.
#:
#: Derived from the status alone, so nothing here can leak: the response body is never consulted.
#: 429 has its own entry because it is the one a free-tier key hits in ordinary use, and reporting
#: that as a generic failure sends the user to check their key and their network when neither is
#: wrong. Found by running it - the quota ran out while verifying Gemini.
_HTTP_REASONS = {
    400: "the provider rejected the request as malformed",
    401: "the provider did not accept the key. Check the stored key, or switch back to Local mode",
    403: "the key was refused for this model. It may not be enabled for this account",
    404: "the provider has no such model. The pinned model name may have been retired",
    429: (
        "the provider's rate limit or quota has been reached. Nothing is wrong with the key - wait "
        "and ask again, or switch to Local mode, which has no quota"
    ),
    500: "the provider reported an internal error",
    503: "the provider is temporarily unavailable",
}


def _http_reason(code: int) -> str:
    known = _HTTP_REASONS.get(code)
    if known is not None:
        return known

    # A range rather than a bare code, so an unlisted status still says whose fault it is.
    if 500 <= code < 600:
        return f"the provider reported a server error (HTTP {code})"
    return f"the provider refused the request (HTTP {code})"


def post_json(
    url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None, timeout: int = DEFAULT_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """POSTs JSON and returns the parsed response.

    Blocking. Callers on the event loop run it through ``asyncio.to_thread``, the same way approved
    capability handlers already do.
    """
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - url is pinned by the caller
            body = response.read()
    except urllib.error.HTTPError as error:
        # The status and what it means, never the body. A provider's error body can echo the request,
        # and the request carries a key in a header on the cloud path. The code itself is safe, and
        # on its own it is useless to whoever is reading the panel: "HTTP 429" sends somebody to
        # check their network when what actually happened is that they ran out of free quota.
        raise ProviderError(_http_reason(error.code)) from None
    except OSError as error:
        # EgressBlocked is an OSError, and urllib wraps every OSError from the socket layer in a
        # URLError - so without unwrapping, the product's own boundary reports as a dead provider
        # and sends the user to check their network, their key and a status page. Found by running
        # it.
        blocked = _egress_refusal(error)
        if blocked is not None:
            raise ProviderError(f"the request was refused before it left this machine: {blocked}") from None

        # Otherwise the class name rather than the message, for the same reason as above.
        raise ProviderError(f"the provider could not be reached: {type(error).__name__}") from None

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        raise MalformedOutput("the provider's response was not JSON") from None

    if not isinstance(parsed, dict):
        raise MalformedOutput("the provider's response was not a JSON object")

    return parsed


def _egress_refusal(error: OSError) -> str | None:
    """The boundary's own message, dug out of whatever wrapped it.

    Imported here rather than at module scope: the provider layer must not depend on the guard being
    installed, and a module-level import would make the network boundary a construction-time
    dependency of every provider.
    """
    from local_zero_brain.net.egress import EgressBlocked

    if isinstance(error, EgressBlocked):
        return str(error)

    reason = getattr(error, "reason", None)
    return str(reason) if isinstance(reason, EgressBlocked) else None


def _unfence(raw: str) -> str:
    """Strips a ``` fence if there is one.

    Models wrap JSON in fences constantly. Retrying over something this mechanical would spend a
    round trip on a formatting habit.
    """
    text = raw.strip()
    if not text.startswith("```"):
        return text

    without_open = text.split("\n", 1)[-1]
    return without_open.rsplit("```", 1)[0].strip()
