"""The cloud provider. The only code in Local Zero that intends to leave the machine.

docs/SECURITY.md section 11 is precise about the division of labour, and this module is the second
mechanism in that table: it pins one base URL and sends the key in a header. It is **not** what
prevents a connection to somewhere else - the egress guard cannot enforce a hostname, because by the
time a socket connects the name has already been resolved to a rotating anycast address. What this
class contributes is that Local Zero's own request has one destination by construction.

Two things are deliberately absent:

* **No embeddings.** Indexing the vault means embedding its entire contents, so an embedding call
  crossing the network would ship the user's notes to a provider one chunk at a time. Embeddings are
  local in every mode, and the refusal is code rather than a comment.
* **No key in any string this module produces.** The key lives in a ``Secret``, and the only place
  it is revealed is the request header.
"""

from __future__ import annotations

from collections.abc import Sequence

from local_zero_brain.credentials import Secret
from local_zero_brain.llm.provider import MalformedOutput, post_json

#: Pinned. Not assembled from configuration, and not overridable at construction - an endpoint a
#: caller can set is an endpoint an untrusted value can eventually set.
BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

#: Verified against the live API on 2026-08-12 - which is also how the previous default was found to
#: be wrong. ``gemini-2.0-flash`` had been pinned on a guess and no longer exists: it is absent from
#: everything ``/v1beta/models`` reports as supporting generateContent.
#:
#: Chosen by measurement rather than by version number. Five planner turns each:
#:
#:     gemini-2.5-flash   5/5 correct   median 0.82s   max 1.00s
#:     gemini-3.5-flash   5/5 correct   median 1.94s   max 6.00s
#:     gemini-3.6-flash   correct, but 25s on a cold first call
#:     gemini-2.5-pro     HTTP 404 - listed by the API, not reachable with this key
#:
#: The planner runs on every turn and the user waits on it, so once correctness ties latency is what
#: decides. A newer model is not automatically a better planner.
#:
#: An explicit version, never a ``-latest`` alias: an alias would swap the model under a prompt tuned
#: for strict JSON without anything in this repository changing. Same reasoning as BASE_URL above.
DEFAULT_MODEL = "gemini-2.5-flash"


class GeminiProvider:
    """Chat against one pinned endpoint."""

    __slots__ = ("_key", "_model")

    name = "gemini"

    def __init__(self, *, key: Secret, model: str = DEFAULT_MODEL) -> None:
        self._key = key
        self._model = model

    @property
    def base_url(self) -> str:
        return BASE_URL

    @property
    def model(self) -> str:
        return self._model

    def __repr__(self) -> str:
        # __slots__ already keeps the key out of a __dict__; this keeps it out of a traceback frame
        # summary and out of anything that prints the object.
        return f"GeminiProvider(model={self._model!r})"

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        payload: dict[str, object] = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
        if system is not None:
            # Its own field, not prepended to the user's text. Same rule as the local provider.
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        response = post_json(
            f"{BASE_URL}/models/{self._model}:generateContent",
            payload,
            headers={"x-goog-api-key": self._key.reveal()},
        )

        return _first_text(response)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError(
            "embeddings are computed locally in every mode. Indexing the vault through a network "
            "provider would send its contents off the machine one chunk at a time"
        )


def _first_text(response: dict) -> str:
    """Reads the answer, refusing anything that is not the shape it expects.

    A response with no candidates is the shape a safety block takes, and returning "" for it would
    present a refusal as an empty answer.
    """
    candidates = response.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise MalformedOutput("the provider returned no candidates")

    parts = candidates[0].get("content", {}).get("parts")
    if not isinstance(parts, list) or not parts:
        raise MalformedOutput("the provider's first candidate had no content")

    text = parts[0].get("text")
    if not isinstance(text, str):
        raise MalformedOutput("the provider's response had no text")

    return text
