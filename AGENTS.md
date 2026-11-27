# AGENTS.md — APEX Lab Hub

- This repository owns orchestration, not sporting rules, asset generation, or scientific algorithms. Reuse domain tools; do not move or duplicate their implementations.
- Review exact code, existing contracts, target AGENTS.md, branch and tests before changes. One principal capability per increment. Record source SHA, results, limits and rollback.
- Task JSON is untrusted data: no shell, arbitrary executable, path, credentials, profile escalation or network destination. Operator binds reviewed clean checkouts separately. Explicit trust is not a sandbox.
- Least privilege; no personal runner or permanent services without owner approval, no secrets in logs, no automatic publication or merge, no paid resources without approval. Public repo: never copy private Architect code, assets or evidence here.
- Hub CI verifies Hub contracts; game FAST on exact SHA remains mandatory for changes to game, FULL only explicitly requested. Hyper and Engineering retain their own validation. Do not trigger cross-project writes or run unreviewed candidate code.
- PASS only for actually executed acceptance checks; use FAIL, NOT_RUN, BLOCKED or INCONCLUSIVE otherwise. No blind retries. Keep evidence checksums and versioned contracts.
- No hidden background agent or automatic ChatGPT/Codex access is installed.
