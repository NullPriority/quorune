---
title: "Privacy testing"
status: "current"
authoritative_source: "projection, protocol, pilot, and server privacy tests"
verified: "2026-08-05"
audience: "security, server, client, and pilot contributors"
maintenance: "hand-maintained"
---

# Privacy testing

Privacy tests use deliberately distinct hidden values for every seat so an
accidental shared projection cannot pass by coincidence. For each principal,
assert both what is visible and what is absent from full projections, deltas,
WebSockets, public logs, errors, retry packets, pilot tasks, memory, and durable
sanitized journals.

Required negative surfaces include opposing hands, library order, private
draw/search choices, physical/incarnation IDs, raw capabilities, guest/invite
tokens, analyst artifacts, checkpoints, another pilot's memory, and hidden
event details. Spectators must never receive a decision capability. A
seat-scoped tool or cookie cannot select a different seat after startup/login.

Test reconnect and process restart because fresh delivery paths can bypass an
otherwise correct live projection. Search serialized packets and journals, not
only parsed UI state. Any new state field is private by default until its
visibility contract and negative tests are explicit.

An acting seat's decision `cap` is an authorized opaque token, not card data.
Copy the projected decision and remove only that top-level field before scanning
serialized card-data content for hidden reference substrings; random token bytes
can coincidentally contain short references. Keep capability presence and
nonacting-seat exclusion assertions, and prove that an injected private
reference in a real projection field still fails. Do not omit other fields or
ignore capability exposure to unauthorized principals.
