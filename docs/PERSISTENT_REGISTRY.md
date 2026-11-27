# Persistent registry 1.1

Optional local SQLite registry extends the 1.0 DAG without changing task schema 2 or the capability allowlist. SQLite user_version=1; sessions and append-only history updated in a single transaction (synchronous FULL). One random session ID per run; lock held for lifetime; no reusing a session ID for execution. Each checkpoint stores the complete report, including target SHAs, task dependencies, attempts, outcomes, Hub version, local evidence directory and evidence hashes. Identical checkpoints are deduplicated.

## CLI
```sh
python3 -m hub.orchestrator run contracts/session.example.json --trusted-checkouts \
  --checkout hub /path/to/hub <sha> --checkout game /path/to/game <sha> \
  --checkout hyper /path/to/hyper <sha> --registry ~/.local/state/apex-hub \
  --output /tmp/apex-new-evidence
python3 -m hub.registry --registry ~/.local/state/apex-hub list
python3 -m hub.registry --registry ~/.local/state/apex-hub show <session-id>
python3 -m hub.registry --registry ~/.local/state/apex-hub history <session-id>
python3 -m hub.registry --registry ~/.local/state/apex-hub recover <session-id>
```
Registry directory must be operator-owned mode 0700, outside all target checkouts and separate from evidence. Use a trusted local filesystem, not shared network storage. Keep private registry/evidence outside Git; never publish Architect data. Registry may hold sensitive task text and local paths. No encryption or cross-user authentication.

Lifecycle RUNNING, FINISHED, INTERRUPTED is separate from outcome PASS/FAIL/BLOCKED. Task outcomes retain NOT_RUN for unstarted tasks. Before a job launches, checkpoint BLOCKED/attempts=1; on ordinary completion, store result/evidence. Abrupt process death leaves RUNNING and the last committed checkpoint. Explicit recover acquires nonblocking flock; refuses active sessions, marks abandoned session INTERRUPTED/BLOCKED and preserves completed/unstarted tasks. It does not kill surviving child processes, reconstruct incomplete output, resume, retry or replay. Operator must inspect and explicitly launch a new session if appropriate. Registry is authoritative for committed registered checkpoints; JSON export can lag after a crash. Evidence hashes are recorded, not revalidated by list/show. Unregistered 1.0 CLI runs still work.

Limits: 2 MB serialized snapshot, 16 jobs/300 s existing budget, query list max 100. No retention/pruning, remote synchronization, durable artifact store, distributed lease or background worker; GitHub hosted registry disappears with runner, public smoke exports only public fixture history. Keep DB and evidence backups locally; rollback via revert of 1.1, leave database untouched (1.0 ignores it).

Hosted CI scope after visibility change: Hub and public game only (`contracts/session.ci.json`). Hyper is private and NOT_RUN in hosted smoke; local example retains Hyper for authorized checkouts, unit fixtures retain multi-target coverage. No new credentials or access grants. Engineering, Architect, Foundation and Asset Factory are private and not checked out by CI.
