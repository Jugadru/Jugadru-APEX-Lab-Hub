# Experiment Scheduler 1.4

Single synchronous worker per private local SQLite registry; no daemon, no network or arbitrary executable. Queue capacity 64 queued/running, priority 0–100, FIFO by creation/id within priority, one attempt, exact reviewed Hub SHA. One explicit run-next executes at most one item; different required SHA blocks queue head. Use a separate authorized checkout, trusted local filesystem and a registry outside every checkout. Global flock prevents concurrent cooperating workers; stale RUNNING must be explicitly recovered as INTERRUPTED, never replayed. Cancellation is transactional (queued immediately; running observed every 25ms, best effort); terminal commit honors cancellation even near exit. Child has its own wall alarm; parent process group kill on timeout/cancel, no subprocesses launched by the fixed worker. Crash recovery does not prove orphan death; wait for bounded worker limits to expire before restarting. No auto retry, no remote dispatch.

Only synthetic.calibration is enabled; fixed deterministic Python RNG seed 0..2^32-1, 1..20M samples and optional hit target. Result hash excludes runtime telemetry. This is a reproducibility/resource fixture, not vehicle physics or real science. Record exact Hub SHA, worker digest, Python version, resource readings and evidence hash. CPU RLIMIT_CPU 1..30 seconds soft/hard +1; virtual memory RLIMIT_AS 64..512 MiB; wall 1..60 seconds; file size 64 KiB, core dumps disabled. ru_maxrss is measured peak RSS (Linux KiB), not a hard RSS/cgroup or aggregate multi-process limit. Limits apply inside the fixed trusted worker after interpreter startup. No OS sandbox, cgroups, CPU affinity, distributed scheduling, GPU or hard aggregate quota. Runtime metrics vary; reproducibility is for exact worker/interpreter/seed, not all Python versions. No physics capability until its reviewed adapter, authorization and domain validation are available.

```sh
python3 -m hub.scheduler --registry ~/.local/state/apex-hub enqueue contracts/experiment.example.json --sha <hub-sha>
python3 -m hub.scheduler --registry ~/.local/state/apex-hub run-next --checkout /path/to/hub --sha <hub-sha> --trusted-checkout
python3 -m hub.scheduler --registry ~/.local/state/apex-hub list
python3 -m hub.scheduler --registry ~/.local/state/apex-hub show <id>
python3 -m hub.scheduler --registry ~/.local/state/apex-hub history <id>
python3 -m hub.scheduler --registry ~/.local/state/apex-hub cancel <id>
python3 -m hub.scheduler --registry ~/.local/state/apex-hub recover <id>
```

CI: real fixed subprocesses, reproducible results, priority, persistence, actual CPU/address-space/wall signals, cancellation and recovery. No private repo access added. SQLite additive tables; revert code for rollback, keep database intact. Hosted evidence retention 7 days; local operator manages storage. Prior 1.3 main CI 46026830259 PASS. Foundation App private smoke 46020557584 attempt 2 PASS, unchanged.
