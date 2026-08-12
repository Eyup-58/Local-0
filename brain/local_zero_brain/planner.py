"""The component that may propose an operation, and therefore the one untrusted text never reaches.

docs/SECURITY.md section 2 draws the break here. The planner sees the user's own request and text the
user wrote themselves, and nothing else - not a fetched page, not a file's contents, not a telemetry
string, not an agent-written note, and not any of those summarised, delimited or labelled first.
Summarising is not laundering: a summary of an instruction is still an instruction.

**The type check runs at runtime, not in a type checker.** ``assemble_context`` annotates
``Sequence[TrustedChunk]`` and then verifies it, because mypy is not in the loop when the process is
running and this is the one place where "the annotation says so" is not good enough. Being wrong here
means an executor runs attacker text.

What this component does *not* do is decide whether the operation is safe. It proposes; the guard
disposes. Every proposal goes through ``BrainServices.invoke()`` into the five-step chain, and a
capability the model invented, an argument it made up, or a path it hoped would resolve somewhere
useful is refused there. The planner is not a security control and is not written as one - which is
exactly why it is allowed to be a language model at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from local_zero_brain.capabilities.guard import Invocation
from local_zero_brain.capabilities.registry import CapabilityRegistry
from local_zero_brain.llm.provider import MalformedOutput, Provider, complete_json
from local_zero_brain.memory.chunks import TrustedChunk

PLANNER_SYSTEM = """You plan operations for a local assistant on Windows.

Answer with a single JSON object and nothing else:
  {"capability": "<name from the list>", "args": {...}}
to propose one operation, or
  {"capability": null, "reason": "<short reason>"}
when no listed capability fits.

Every proposal is checked by a guard and shown to the user for approval before anything runs. Do not
describe what you intend to do; name the capability and its arguments."""


@dataclass(frozen=True)
class Proposal:
    """What the planner produced: at most one invocation, or the model's reason for declining.

    Both fields exist because the two outcomes are equally real. An earlier version returned
    ``Invocation | None`` and dropped the reason on the floor, which was fine while nothing but tests
    called this - but the reason is the only thing the brain has to *say* when no capability fits,
    and a UI that has to invent its own "sorry, I cannot help" is a UI that is eventually wrong about
    why. ``reason`` is the model's own words, not a template.
    """

    invocation: Invocation | None
    reason: str | None

    @property
    def declined(self) -> bool:
        return self.invocation is None


class Planner:
    """Turns a request into at most one proposed invocation."""

    __slots__ = ("_provider", "_registry")

    def __init__(self, *, provider: Provider, registry: CapabilityRegistry) -> None:
        self._provider = provider
        #: Used to tell the model what exists. It is not the whitelist - the guard's step 1 is, and
        #: it runs again on whatever comes back regardless of what was offered here.
        self._registry = registry

    def assemble_context(self, chunks: Sequence[TrustedChunk]) -> str:
        """Builds the trusted half of the prompt, refusing anything that is not trusted.

        The refusal is a whole-list refusal. One untrusted item among a hundred trusted ones is
        still the exploit, and partial filtering would leave a caller believing it had passed
        context that was silently dropped.
        """
        for chunk in chunks:
            if not isinstance(chunk, TrustedChunk):
                raise TypeError(
                    "the planner accepts TrustedChunk only. What was passed is "
                    f"{type(chunk).__name__}, and there is no conversion between the two - see "
                    "docs/SECURITY.md section 2"
                )

        if not chunks:
            return ""

        notes = "\n\n".join(f"[{chunk.note_path}]\n{chunk.text}" for chunk in chunks)
        return f"Notes the user wrote:\n\n{notes}"

    def propose(self, request: str, *, context: Sequence[TrustedChunk] = ()) -> Proposal:
        """Asks for one operation. A declined proposal carries the model's reason instead.

        ``origin`` is stamped here, by the brain, at the point the request enters - never taken from
        anything the model said about itself. docs/SECURITY.md section 6.
        """
        assembled = self.assemble_context(context)
        prompt = f"{assembled}\n\nRequest: {request}" if assembled else f"Request: {request}"

        answer = complete_json(self._provider, f"{self._capability_list()}\n\n{prompt}", system=PLANNER_SYSTEM)

        capability = answer.get("capability")
        if capability is None:
            return Proposal(invocation=None, reason=_reason_from(answer))

        if not isinstance(capability, str):
            raise MalformedOutput("the model named a capability that is not a string")

        args = answer.get("args", {})
        if not isinstance(args, dict):
            raise MalformedOutput(f"the arguments for {capability} are not an object")

        return Proposal(
            invocation=Invocation(capability=capability, args=args, origin="user_direct"),
            reason=None,
        )

    def _capability_list(self) -> str:
        lines = [
            f"- {capability.name} ({capability.side_effect})" for capability in self._registry
        ]
        return "Capabilities:\n" + "\n".join(lines) if lines else "Capabilities: none are registered."


#: The longest decline reason that will be carried. Beyond this the model is not explaining, it is
#: rambling, and the caption clamps to three lines on screen regardless.
MAX_REASON_LENGTH = 2000


def _reason_from(answer: dict) -> str | None:
    """The model's stated reason for naming no capability, if it gave one that is usable.

    A missing, blank or non-string reason becomes None rather than a stand-in sentence. None is a
    gap the UI renders as one; a stand-in would put words in the brain's mouth, which is the same
    mistake as a scripted caption and is exactly what ``turn.state.caption`` refuses to spell.
    """
    reason = answer.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return None

    return reason.strip()[:MAX_REASON_LENGTH]
