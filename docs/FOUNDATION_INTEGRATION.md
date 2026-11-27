# Foundation integration 1.2

Foundation and Asset Factory are separate private repositories. The Hub registers both without cloning, issuing credentials or dispatching commands. Source contract reviewed at Foundation SHA c999d8f4062d2df07f1deae55a2252f6262596bf, version 0.1.0, standalone CI 45926447660 PASS. That is upstream evidence, not consumer execution.

```sh
python3 -m hub.orchestrator capabilities --checkout foundation /path/to/foundation <reviewed-sha>
python3 -m hub.orchestrator run contracts/foundation.example.json --trusted-checkouts \
  --checkout foundation /path/to/foundation <reviewed-sha> \
  --registry ~/.local/state/apex-hub --output /tmp/apex-foundation-session
```

Operator supplies authorized clean checkout, exact SHA and trusted execution environment. Fixed adapters call the existing Foundation CLI: validate (catalog and structural acceptance) then list (metadata only). Discovery requires direct successful validation in the same session, checks explicitly reviewed versions 0.1.0 and 0.2.1, counts and exact registry content, produces foundation-capabilities.json with SHA and explicitly non-authorizing metadata. Foundation method strings are never interpreted as commands. Only the known exact foundation.validate method/version/permission maps to an already allowlisted adapter; other entries are DECLARED_ONLY. ADAPTER_MATCH is not execution permission. Unknown versions or invalid output fail closed. Catalog adapter accepts legacy reviewed game snapshots and the new Foundation target. No Asset Factory build, search/compatibility execution, remote dispatch, license validation or private checkout in hosted CI. Module and compatibility validation remains the responsibility of the Foundation validator; consumer checks response envelope and discovery invariants, not a second full schema engine.

Consumer tests run real subprocesses using synthetic fixture providers, not the actual private Foundation repository. Hosted Hub/game smoke remains unchanged. The actual private consumer run is a separate gate, not satisfied by synthetic fixtures. The manual private smoke workflow uses an owner-configured GitHub App; see [setup](PRIVATE_ACCESS.md). A one-time isolated consumer verification may be performed in the Foundation private repository with an exact Hub source snapshot; never merge that harness into Foundation. No private source publication. Private reports remain local and must not be published. Rollback: revert 1.2; old schema 2 reports remain readable.

Reviewed current Foundation 0.2.1 at `1c226891f24796d0a14535693d32f626a5b476f5` (upstream CI 46016905073 PASS): 20 modules, 31 capabilities, 15 active contracts, 10 compatibility pairs. Validation binds the registry digest and discovery must match tracked source metadata. Unknown versions blocked; additional capability methods remain descriptive.
