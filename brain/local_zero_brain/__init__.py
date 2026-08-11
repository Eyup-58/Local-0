"""Local Zero brain layer.

Owns the decisions: the capability registry, the guard chain, the approval queue, the audit log
and LLM routing. It is the only layer that talks to a model.

In M1 none of that exists yet. This milestone builds the transport underneath it - a validated
named-pipe client to the system layer and a WebSocket server for the UI - so that when the guard
arrives it is built on something already known to work.
"""

__version__ = "0.1.0"
