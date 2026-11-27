# Development workflow 1.3

Bounded operator-led lifecycle for Hub only: REQUESTED → AUDITED → PLANNED → AWAITING_CI → REVIEW → APPROVED, or DIAGNOSE → REPAIR_READY → new candidate (at most two repairs; then BLOCKED). Persisted in additional SQLite tables in existing registry. No background process, LLM coding agent, auto-push, automatic PR creation or merge. Operator or explicitly authorized agent performs implementation and pushes a feature branch. Audit records exact base and AGENTS/workflow blobs; operator must actually review rules and plan. Candidate clean checkout must descend from base, remain in declared exact-path scope and isolated feature branch. Repairs must descend from previous candidate and use distinct SHA. Protected paths include workflows, rules, gate code and gate dependencies; changes to these require a separate human-led process. Run the CLI from a trusted integrated Hub installation, not the candidate being audited. Operator must independently verify the trusted base is the intended integrated revision. Raw NUL-delimited Git paths are compared without trimming.

CI receipt queries GitHub REST directly using caller-provided APEX_CI_READ_TOKEN (Actions read on Hub only). Fixed repository, fixed workflow ID/path, push event, exact SHA/branch, same head repository, attempt-specific jobs, required completed steps. No redirect, arbitrary URL, raw log ingestion or credentials stored. Success only if required checks actually passed; failure diagnosis identifies failing step names (not root cause); skipped/cancelled/in-progress are inconclusive. A terminal failure requires explicit diagnosis and new commit, never blind rerun. The checksum records normalized receipt, not cryptographic attestation. API response trusted under HTTPS; local operator/DB are trusted. Approval is a local declaration tied to SHA, not verified GitHub reviewer identity; no merge authorization. Recheck current remote base/head, branch protection and GitHub reviews before any real PR. No game FAST policy invented: other repos not enabled for this development gate until their actual workflow contracts are reviewed. Foundation App retains Contents read only, no new scope or secret.

## CLI example
```sh
python3 -m hub.development --registry ~/.local/state/apex-hub request --objective "Improve docs" --base <sha> --path README.md
python3 -m hub.development --registry ~/.local/state/apex-hub audit <id> --checkout /path/to/hub
python3 -m hub.development --registry ~/.local/state/apex-hub plan <id> --note "Edit README only"
# Operator creates reviewed feature branch, implements, commits and pushes outside Hub.
python3 -m hub.development --registry ~/.local/state/apex-hub candidate <id> --checkout /path/to/hub --sha <candidate-sha>
# Supply authorized read token through environment, never chat or command line.
python3 -m hub.development --registry ~/.local/state/apex-hub ci <id> --run-id <push-run-id>
python3 -m hub.development --registry ~/.local/state/apex-hub diagnose <id> --note "Observed failure and proposed fix"
python3 -m hub.development --registry ~/.local/state/apex-hub approve <id> --sha <candidate-sha> --note "Reviewed exact diff and evidence"
python3 -m hub.development --registry ~/.local/state/apex-hub draft <id>
python3 -m hub.development --registry ~/.local/state/apex-hub history <id>
```

CI uses existing job with additional Actions read only to verify a real previously integrated run (46017566189) on exact SHA; no self-referential success claim. Synthetic tests verify transitions, repair limits, scope, invalid receipts and persistence. No actual PR writes from module. Local operator must keep registry outside checkout. Rollback revert code; new tables remain ignored by 1.2. Foundation App workflow remains unchanged; private smoke run 46020557584 attempt 2 PASS, token revoked.
