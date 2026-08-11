"""Pydantic mirrors of the JSON Schema contracts.

The schemas under contracts/ are the single source of truth; these models are a mirror of them,
and where the two disagree the schema is right and the model is a bug. test_contract_models.py
holds them together by running every checked-in example through the models - the valid ones must
parse and the rejected ones must fail.

Two properties are load-bearing and appear in every model here:

* ``extra="forbid"`` at every level. The schemas set ``additionalProperties: false`` everywhere,
  so an unknown field is a rejected message rather than an ignored one. That is what stops a field
  being smuggled past one layer in the hope that a later one reads it.
* Validation happens before any field is read. A message that fails validation has no readable
  fields, including for logging.
"""
