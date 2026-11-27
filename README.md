# APEX Lab Hub — 1.6.0

Autonomous Development Orchestrator for the APEX Motorsport simulation ecosystem.

Independent control plane. Migrates APEX-Simulation PR #183 (head a63d62224cbdbfb71f1993e51ca11bdba8cda5db); the existing game #141 bridge remains a domain-local command interface, not a competing Hub. No game code or private Architect source copied.

## Run (Linux, Python 3.10+, Git)
```sh
python3 -m hub.orchestrator capabilities
python3 -m hub.orchestrator plan contracts/session.example.json
python3 -m hub.orchestrator run contracts/session.example.json --trusted-checkouts \
  --checkout hub /path/to/hub <hub-sha> \
  --checkout game /path/to/game <game-sha> \
  --checkout hyper /path/to/hyper <hyper-sha> \
  --output /tmp/apex-hub-new-session
python3 -m unittest discover -s tests -v
```
Replace paths/SHAs with reviewed, clean clones; output directory must not exist and must be outside all checkouts. The CLI does not clone, fetch or infer trust. Run reviewed code only in an isolated authorized environment. Discovery lists available capabilities, not successful results.

For durable local sessions, add `--registry ~/.local/state/apex-hub`; use `python3 -m hub.registry --registry ~/.local/state/apex-hub list` to query them. See [persistent registry](docs/PERSISTENT_REGISTRY.md) for recovery and privacy limits.

See [operating rules](AGENTS.md), [architecture and migration](docs/ARCHITECTURE.md), [roadmap](ROADMAP.md), [changelog](CHANGELOG.md). JSON schema 2 adds target to each task. Existing game FAST is unchanged. No remote dispatch, permanent services or auto-merge.

Hosted smoke uses `contracts/session.ci.json` (Hub + public game); private Hyper is not run by hosted CI. The local full example remains available for authorized checkouts.

Foundation integration: [read-only discovery contract](docs/FOUNDATION_INTEGRATION.md); private authorized checkouts only.

Development lifecycle: [operator-led exact-SHA CI and review workflow](docs/DEVELOPMENT_WORKFLOW.md).

Experiment scheduler: [bounded local queue](docs/EXPERIMENT_SCHEDULER.md).

Graphics: [Asset Factory orchestration](docs/GRAPHICS_WORKFLOW.md).

Research: [Foundation and Engineering orchestration](docs/RESEARCH_ORCHESTRATOR.md).
