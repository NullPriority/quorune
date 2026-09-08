---
title: "Replay testing"
status: "current"
authoritative_source: "record replay tests and local merge gate"
verified: "2026-08-05"
audience: "engine, persistence, and protocol contributors"
maintenance: "hand-maintained"
---

# Replay testing

Every state-changing rules or protocol change needs a replay witness at the
lowest practical level. Build a deterministic initial state, submit canonical
commands through the same public boundary used by clients, persist the record,
replay it, and compare authoritative state hashes and lifecycle results.

Also test rejection and rollback: an invalid target, stale capability, changed
cost, illegal payment, or malformed choice must leave no partial mutation or
accepted command. When persistence or idempotency changes, test a lost response
and exact command retry. When schema/fingerprint behavior changes, test both a
matching load and the intended fail-closed mismatch.

Do not “fix” Game Record v3 by editing a saved command or checkpoint. Historical
private records stay local; public fixtures contain sanitized recipes and no
capabilities or hidden library order. See the [replay architecture](../architecture/replay.md)
and the [Game Record reference](../reference/game-record.md).

Old data being readable and old behavior being reproducible are separate
claims. When persisted semantics change, add one focused historical-record
witness for the supported compatibility boundary. Preserve the historical
execution result or reject the record explicitly according to current policy;
never silently reinterpret it. A compiler-version change or successful replay
recorded only under the current version is not historical-compatibility
evidence.
