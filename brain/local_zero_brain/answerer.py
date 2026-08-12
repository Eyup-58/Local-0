"""The component that answers from the user's own notes, and has nothing to act with.

This is the fourth quadrant of the design docs/SECURITY.md section 2 sets up. Three of them already
existed:

===============  ==========================  ==============================
                 trusted context             untrusted context
===============  ==========================  ==============================
can act          ``Planner``                 forbidden by section 2
prose only       **this module**             ``Reader``
===============  ==========================  ==============================

The ``Reader`` could not fill this slot, and the reason is worth stating because reaching for it is
the obvious mistake: ``Reader.read`` **refuses** a ``TrustedChunk`` on purpose - "the break runs both
ways" - and its system prompt frames its input as text retrieved from outside the machine. Handing it
the user's own notes would mean treating them as a stranger's text, and getting it to accept them
would mean deleting the check that stops a stranger's text being treated as the user's.

So this class is the mirror of ``Reader``, and inherits its one real safety property: **it cannot
act.** No registry, no invocation, no path to an executor. It returns ``str``. Whatever the model
answers - including a perfectly formed capability invocation - comes back as prose for a human to
read, and the only thing the brain does with it is put it in a caption.

What that buys: the model may be fully persuaded by anything in the vault and nothing happens. That
matters less here than in the ``Reader``, because a trusted note is by definition one the user wrote
themselves - but "the user wrote it" is a claim about provenance, not about content, and a note the
user pasted from somewhere is still a note the user wrote into a trusted folder. The wall costs
nothing and removes the question.
"""

from __future__ import annotations

from collections.abc import Sequence

from local_zero_brain.capabilities.registry import CapabilityRegistry
from local_zero_brain.llm.provider import Provider
from local_zero_brain.memory.chunks import TrustedChunk

ANSWERER_SYSTEM = """You answer the user's question using their own notes.

Answer only from the notes provided. When they do not contain the answer, say so plainly - do not
fill the gap from general knowledge, and do not guess. Naming the note a claim came from is useful;
inventing one is not.

Answer in the language the user asked in. Be brief: two or three sentences unless the question needs
more. You are answering, not planning - you have no tools and no way to act, so do not describe
operations you would perform or offer to perform them."""


class Answerer:
    """Answers questions from the user's own notes. Returns prose, and can do nothing else."""

    __slots__ = ("_provider", "_registry")

    def __init__(self, *, provider: Provider) -> None:
        self._provider = provider
        #: Empty, and there is no way to make it otherwise - the same rule as the reader's, for the
        #: same reason. A constructor taking a registry is one keyword argument away from being
        #: handed a full one in a call that would look reasonable in review.
        self._registry = CapabilityRegistry([])

    @property
    def registry(self) -> CapabilityRegistry:
        return self._registry

    def answer(self, question: str, chunks: Sequence[TrustedChunk]) -> str:
        """Answers about the given notes. Refuses untrusted chunks - the break runs both ways.

        An ``UntrustedChunk`` arriving here would mean a fetched page or an agent-written note was
        about to be presented to the user as their own memory, under a system prompt that calls it
        "their own notes". That is the same class of mistake as untrusted text reaching the planner,
        one step further along: it would not cause an action, it would cause the user to believe
        something about where an answer came from.
        """
        for chunk in chunks:
            if not isinstance(chunk, TrustedChunk):
                raise TypeError(
                    "the answerer accepts TrustedChunk only. What was passed is "
                    f"{type(chunk).__name__}, and there is no conversion between the two - see "
                    "docs/SECURITY.md section 2"
                )

        return self._provider.complete(
            f"{_notes(chunks)}\n\nQuestion: {question}", system=ANSWERER_SYSTEM
        )


def _notes(chunks: Sequence[TrustedChunk]) -> str:
    """The notes, each labelled with the file it came from so the answer can cite it.

    Says so explicitly when there are none. An empty block would leave the model to infer whether it
    had been given nothing or given everything, and the honest answer to a question the vault cannot
    answer is that it cannot answer it.
    """
    if not chunks:
        return "The user's notes:\n\n(no note matched this question)"

    blocks = "\n\n".join(f"[{chunk.note_path}]\n{chunk.text}" for chunk in chunks)
    return f"The user's notes:\n\n{blocks}"
