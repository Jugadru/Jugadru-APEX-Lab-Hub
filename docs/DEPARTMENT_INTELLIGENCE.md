# Department Intelligence 2.1

Deterministic advisory planning, not independent AI. Eight curated profiles with technical guidelines and the existing Gateway permission matrix. Explicit topic (auto or a department), capability and target determine the assignment; free-text objectives never authorize commands. Validate exact SHA, fixed task fields, priorities, dependencies, acceptance and Gateway budget. Auto routing uses reviewed static preference, not ML. Topic outside the authorized matrix is rejected. Quality reviews non-quality tasks; Software reviews quality-owned tasks in an advisory role, without execution permission expansion. All review roles must accept exact plan digest; local operator declaration, not authenticated or independent human identities. Rejection is terminal. Atomic handoff to existing Gateway produces PROPOSED case only; separate Gateway approval and execution required. At most 32 active planning cases, same 8-task/180s Gateway limit. Observe exact linked case once, verify report identity and underlying artifact bytes before recording task status counts and conservative follow-up suggestions. No inferred root cause, auto retry, staff ranking, FPS claim or productivity rating. No evidence means no observation; failed/cancelled cases are not transformed into PASS. Metrics historical, not a fresh byte audit. Curated guidelines are not a searchable external knowledge base. No provider credentials or game writes.

```sh
python3 -m hub.departments --registry ~/.local/state/apex-hub catalog
python3 -m hub.departments --registry ~/.local/state/apex-hub propose contracts/department.example.json
python3 -m hub.departments --registry ~/.local/state/apex-hub review <id> --plan-sha256 <digest> --reviewer quality --decision accept --note "reviewed"
python3 -m hub.departments --registry ~/.local/state/apex-hub delegate <id> --plan-sha256 <digest>
# Use existing hub.gateway approve/run with returned gateway_id and its own plan digest.
python3 -m hub.departments --registry ~/.local/state/apex-hub observe <id>
python3 -m hub.departments --registry ~/.local/state/apex-hub metrics
```

Replace example SHA with reviewed checkout. New additive SQLite tables; dashboard projection; rollback revert code, preserve records. Existing mission direct Gateway route remains available; no mandatory new gate imposed on previous contracts. Private mission 2.0.1 run 46066589740 on exact integrated 97abb9ef96492a54a4b3ea0567fe2bf29138b36f PASS all nine stages, run_fixture=true, include_graphics=true, both tokens revoked. Artifact 15231635005 SHA256 b1662e4154a8452a31a49038c8dc56acd119473369623aa835a6b9bccf60baf1. Main CI 46065729586 PASS. Graphics preflight only; no native render/GPU benchmark or independent GLB audit. External agents not configured.

Terminal linked Gateway cases with missing/invalid evidence can be explicitly closed with `close-unverified <id> --note "reason"`. Closure is INCONCLUSIVE, not an observation, excluded from task metrics, preserves Gateway outcome and prevents replay. A verifiable case must use observe. No automatic repair or retry.
