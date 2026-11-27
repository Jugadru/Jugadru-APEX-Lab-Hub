# Agent Gateway 1.7

Eight specialized roles: software, physics, ai, racecraft, graphics, research, performance, quality. Fixed policy maps roles to reviewed local diagnostic capabilities/targets. No independent model agents or authenticated identities yet: local operator assigns roles and relays messages. Messages are inert notes, not instructions; only participants, up to 24 per case. Domain owners retain authority. Registry provides persistent proposals, exact plan digest, explicit local operator approval, one attempt, history, dependency handoffs, cancellation before launch and recovery without replay. Up to 8 tasks/180 declared seconds and 32 active cases. Execution revalidates exact target SHAs and clean origins, invokes existing DAG engine (read-only) and retains receipt hash. Timeout is subprocess budget, not hard aggregate CPU/RAM/wall isolation. No automatic writes or merge. Existing research, graphics, scheduler and development workflows remain separately operator-controlled; this gateway does not yet invoke them. No remote dispatch, MCP, ChatGPT/Codex or generic API provider. GitHub REST CI receipt uses existing fixed read-only adapter, explicit run ID/SHA/branch, APEX_CI_READ_TOKEN passed in environment; no credential persistence or new grants. INCONCLUSIVE is terminal; new explicit case needed to observe another run. Local operator/database trusted; not RBAC authentication or a security sandbox.

```sh
python3 -m hub.gateway --registry ~/.local/state/apex-hub catalog
python3 -m hub.gateway --registry ~/.local/state/apex-hub propose delegation.json
python3 -m hub.gateway --registry ~/.local/state/apex-hub approve <id> --plan-sha256 <digest> --note "reviewed"
python3 -m hub.gateway --registry ~/.local/state/apex-hub run <id> --plan-sha256 <digest> --trusted-checkouts --checkout hub /path/to/hub <sha>
python3 -m hub.gateway --registry ~/.local/state/apex-hub history <id>
```

Delegation JSON schema 1 has objective, targets mapping exact SHAs, tasks with existing orchestrator task fields plus department; see contracts/delegation.example.json (replace example SHA). No paths/commands/secrets in task JSON. Provider catalog distinguishes operational adapters from NOT_CONFIGURED. Actual hosted test runs two departments through existing Hub/game DAG and a real GitHub CI receipt of integrated 1.6. Revert code for rollback; SQLite tables additive.

## Graphics department policy

Use independently created geometry inspired by reference photographs and era rules, or adapt assets whose license or express permission permits the intended modification and redistribution. Record author, source URL, exact revision/file hash, license/permission, attribution, redistribution/derivative obligations, and modifications. Changing another creator's model, painting, textures, or topology does not establish ownership or clear rights; mod availability is not permission. Asset Factory current original-only gate stays unchanged. External asset intake requires separate reviewed contract before import. No external model copied in 1.7.

## Native and research evidence carried forward

Owner reports native build 40d6edf25f934b2aa9b9932c4e3cbc78 and build/capture 6bb6b84d034a417d8b8232e8fac4971a, both PASS; second capture 960x540, 2952 triangles, 28 meshes, five materials, no textures. Software render only, visual assessment prototype acceptable, production quality not approved. First and second GLB hashes differ, so byte-level determinism not established. Owner supplied JSON and screenshot/capture, not actual GLB bytes; independent binary verification NOT_RUN. No promotion. Combined private research smoke 46045868747 PASS and token revoked; 1.6 main CI 46045078465 PASS.
