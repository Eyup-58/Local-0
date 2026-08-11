"""The capability registry, the guard chain, and the handlers they admit.

Built in M2 deliberately before any LLM exists. If the guard is proven correct while nothing can
talk to it, then when M4 adds a model, a failure is isolated to the model layer rather than being
ambiguous between the two. See docs/ROADMAP.md.
"""
