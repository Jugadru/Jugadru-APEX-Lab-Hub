# Mission Control Center 2.9

Read-only local aggregation of the existing dashboard; eight source registries maximum. Eight module groups, coverage NOT_RUN/EMPTY/RECORDED, complete stored lifecycle counts (not unique missions, active agents or scientific outcomes), sampled recent review queue (20 latest per table per registry, max 60, explicit truncation), latest infrastructure observation per registry. Preserve separate state/outcome for observed work. Historical FAIL and interrupted records are review candidates, not automatically open incidents. Health snapshots lack aggregate states and are reported unclassified. No inferred GPU success, no live polling, no automatic approvals, fixes, commands, network, credentials or background service. Uses existing safe projected metadata and HTML escaping; local trusted SQLite, snapshots individually consistent, not cross-registry atomic. Export new private directory only, four files; verification rederives summary and HTML from saved dashboard JSON, not a new audit of source records and not a signature. Interrupted export can leave partial directory; verify rejects it. Public CI uses only synthetic/public domain evidence. Never publish actual private outputs.

```sh
python3 -m hub.mission_control export --registry missions /path/to/mission-state --registry research /path/to/research-state --output ~/apex-control-snapshot
python3 -m hub.mission_control verify ~/apex-control-snapshot
xdg-open ~/apex-control-snapshot/mission-control.html
```

Add up to eight distinct existing registry bindings, no missing paths fabricated. Detailed dashboard index.html remains available. Re-export to a new directory for updated data. No new agents, GUI server or changes to game/assets. Owner authorized 2.9 after 2.8 candidate passed both workflows; merged tree identical, main CI 46084445652 initially in progress. Rollback revert code; no registry schema changes. Physical GPU, original GLB and external model integration remain separate.

Correction: initial exact candidate e88b1cb5081c76744c3e9f751a3e835529f52c40 failed both integration runs 46084540249 and 46084545781 after 134 unit tests passed. Dashboard UNKNOWN for absent collaboration outcome incorrectly overrode PROPOSED. Interpret outcomes only at applicable terminal lifecycle; running session BLOCKED placeholder is not a completed outcome. Keep UNKNOWN terminal observations as attention. Three regression tests and original integration gate retained. 2.8 main 46084445652 PASS.
