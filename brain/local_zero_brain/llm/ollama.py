"""The local provider. Loopback only, and the only one that computes embeddings.

The model server is already on this machine at ``127.0.0.1:11434``. That address is why
docs/SECURITY.md section 11 states the boundary as *no non-loopback egress* rather than "no
network": a rule the product's own local path violates on first run is a rule that gets edited
instead of obeyed.

Section 11 also records something about this machine that is not Local Zero's to fix -
``OLLAMA_HOST`` is set to ``0.0.0.0:11434`` here, so the user's model server listens on every
interface and is reachable from their LAN. Local mode guarantees *Local Zero* sends nothing off the
machine. It says nothing about what else on the machine is listening.
"""

from __future__ import annotations

from collections.abc import Sequence

from local_zero_brain.llm.provider import MalformedOutput, post_json

DEFAULT_BASE_URL = "http://127.0.0.1:11434"

#: Verified present and answering on this machine, 2026-08-12. A model that is not installed fails
#: with the server's own message, which names the model - better than anything this layer could
#: invent.
DEFAULT_MODEL = "gemma4:26b"

#: Small, local, and used only for the vault index. Kept separate from the chat model because an
#: index built with one embedding model is not comparable to vectors from another.
#:
#: NOT INSTALLED as of 2026-08-12 - `ollama pull nomic-embed-text` before memory can rank by meaning.
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"

#: Longer than the shared default, because the local path and the network path fail differently.
#:
#: A remote provider that has not answered in a minute is not going to. A local one might simply be
#: slow: measured 2026-08-12 on this machine, a reader answer over one small chunk took **55.6s** -
#: gemma4:26b is 17 GB against 16 GB of VRAM, so it spills to CPU - and a cold load adds roughly 18s
#: on top. At the shared 60s default that request timed out twice before being measured, and it
#: reported as "the provider could not be reached", which is the wrong thing to tell somebody whose
#: model is merely thinking.
LOCAL_TIMEOUT_SECONDS = 300


class OllamaProvider:
    """Chat and embeddings against the local server."""

    __slots__ = ("_base_url", "_model", "_embedding_model", "_timeout")

    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        timeout: int = LOCAL_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._embedding_model = embedding_model
        self._timeout = timeout

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def model(self) -> str:
        return self._model

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        """One turn. The system text is a separate message, never concatenated into the prompt.

        Concatenation is how the boundary between two kinds of text stops existing, and that
        boundary is what the threat model rests on.
        """
        messages = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = post_json(
            f"{self._base_url}/api/chat",
            {"model": self._model, "messages": messages, "stream": False},
            timeout=self._timeout,
        )

        content = response.get("message", {}).get("content")
        if not isinstance(content, str):
            # An empty answer and a response of an unexpected shape are different facts, and
            # returning "" for both would hide a broken integration behind a model that said
            # nothing.
            raise MalformedOutput("the local model's response had no message content")

        return content

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Vectors for the vault index. Never leaves the machine, in either mode."""
        response = post_json(
            f"{self._base_url}/api/embed",
            {"model": self._embedding_model, "input": list(texts)},
            timeout=self._timeout,
        )

        embeddings = response.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise MalformedOutput(
                "the local model returned a different number of embeddings than the texts it was "
                "given, so no vector can be matched to its chunk"
            )

        return embeddings
