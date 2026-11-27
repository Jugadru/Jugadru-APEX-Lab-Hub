# Changelog

## 1.2.0 — Foundation Integration candidate

- Separate Foundation and Asset Factory targets, validation and metadata discovery via fixed upstream CLI, direct dependency gate, version and count checks. No execution authorization from metadata.
- Explicitly reviewed Foundation 0.2.1 support; match list output to validated registry digest. Manual private read-only smoke workflow; no credential created or installed.

## 1.1.0 — Persistent Task Registry

- Optional private local SQLite sessions, atomic checkpoint/history updates, query CLI and explicit lock-guarded interruption recovery; no replay.
- Cross-project CI verifies durable report in a separate process. Schema 2 task contracts and all domain capabilities unchanged.

## 1.0.0 — candidate

- Migrated PR #183 orchestration core to independent Hub; schema 2 multi-target contracts, trusted checkout bindings, Linux bounded executor, cross-project evidence and CI.
- No game runtime modifications, private code, or remote dispatch. Validation must be checked against exact Hub SHA.
