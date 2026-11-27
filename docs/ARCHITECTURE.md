# Architecture / migration

Source: APEX-Simulation PR #183, head a63d62224cbdbfb71f1993e51ca11bdba8cda5db. Prior PASS: 53 contracts including seven new tests and actual game DAG on synthetic merge SHA 65ff7c93a979ff98cb754e480ed046dd678ed7bb. Historical only, not evidence for this revision.

New design: agent -> Hub CLI -> strict task schema 2 -> deterministic DAG -> reviewed target bindings -> allowlisted adapters -> per-task evidence/report. Scope and objective are descriptive, never executable. Exact HEAD, matching origin and clean tracked/untracked status are checked when binding; HEAD/clean status rechecked around jobs. This is not cryptographic origin authentication, proof of review, or a sandbox; ignored files and local Git configuration remain operator trust assumptions.

Registry:
- hub: Jugadru/Jugadru-APEX-Lab-Hub (public)
- game: Jugadru/APEX-Simulation (public; #141 bridge retained)
- hyper: Jugadru/APEX-Hyper-Engine (public; revision adapter only)
- engineering: Jugadru/APEX-Engineering-Lab (public; README-only at audit)
- architect: Jugadru/apex-godot-simulation-architect (private; no source migration; revision adapter only)
- Foundation remains game PR #177 (open at audit); catalog adapter blocked until reviewed files exist. Asset Factory not executed.

Capabilities: repo.revision (all), source.inventory (game), architecture.audit (game), foundation.catalog (game). Reuse exact domain Python scripts without copying. 16 tasks, 64 KB input, 300 s summed job timeout budget; one attempt, deterministic priority, independent branches may continue. No actual CPU/RSS quota, durable queue or parallel executor. Reports contain per-target SHA and SHA-256 hashes; local CLI is synchronous, Linux process-group timeout. CI uses separate hosted checkouts, pinned public game/hyper revisions, no credentials persisted. Official domain CI remains authoritative. Rollback: revert Hub migration commit; original PR is historical, not required to run Hub.

Integration next: typed GitHub read/run receipts, capability discovery from Foundation, persistent sessions, experiment scheduling, diagnostics and bounded agent repair, graphics/research adapters. Remote dispatch requires supported authorization; private data stays private.

1.1 adds optional local durable session history; see [persistent registry](PERSISTENT_REGISTRY.md). No distributed scheduling or retries.

Visibility update: Hub, Hyper and Engineering are now private; separate private APEX-Foundation and apex-asset-factory repositories exist. Hosted CI uses Hub and public game only; Hyper is NOT_RUN there. Foundation adapter migration is planned for 1.2. Historical visibility and prior smoke evidence above are not current access claims.
