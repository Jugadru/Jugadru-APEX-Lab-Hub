# Graphics Workflow 1.5

Hub coordinates the existing Asset Factory, never replaces the generator. Initial allowlist is the reviewed Factory 0.1.0 exact SHA 640b037b30fbe729885867bf51ea6d40d0b66466 (standalone CI 46026871347 PASS). Source hashes include build/schema/demo/vehicle/staging and shared helpers; exact clean checkout and origin required. Any other Factory SHA blocked until reviewed. Request validation and Foundation plan delegated to existing factory code. No arbitrary shell, third-party download, promotion, automatic PR, or changes to game. Private SQLite stores job snapshots and evidence hashes, new private workspace for every run, explicit interrupted recovery no replay.

```sh
python3 -m hub.graphics --registry ~/.local/state/apex-hub plan --checkout /path/to/asset-factory --sha 640b037b30fbe729885867bf51ea6d40d0b66466 --request /path/to/asset-factory/tools/asset_factory/demo.json --trusted-checkout
python3 -m hub.graphics --registry ~/.local/state/apex-hub build --checkout /path/to/asset-factory --sha 640b037b30fbe729885867bf51ea6d40d0b66466 --request /path/to/asset-factory/tools/asset_factory/demo.json --trusted-checkout --blender /path/to/blender --blender-sha <binary-sha256> --godot /path/to/godot --godot-sha <binary-sha256> --capture
python3 -m hub.graphics --registry ~/.local/state/apex-hub list
python3 -m hub.graphics --registry ~/.local/state/apex-hub show <id>
python3 -m hub.graphics --registry ~/.local/state/apex-hub recover <id>
```

Native build explicitly opt-in, owner supplies trusted binaries and exact executable (not archive) SHA256; compute via sha256sum after verifying archives against Factory docs/RUNTIMES.md. Archive hashes identify owner-supplied copies, not independent publisher signatures. Runtime version checks delegated to Factory. Private workspace, sanitized env, outer 480s timeout/process-group kill, bounded receipt logs, no detached children intended. This is not OS sandbox, CPU/RAM/disk quota or hard log cap for nested tools. Use disposable environment, no personal runner without approval. Capture requires Xvfb, forces software rendering, not physical GPU. Asset acceptance verifies Factory stage markers, artifact hashes, triangle budget, untextured fixture, dimensions and import, registry provenance; partial GLB and PNG QA remain Factory scope, no duplicate full validator. Human visual review NOT_RUN and promotion BLOCKED even on PASS. Licenses/quality/PBR/LOD, GPU, actual game import and game FAST are separate. If native not executed, never infer from preflight or historical native evidence.

Hosted unit tests use synthetic fixture. One-time isolated private Factory consumer test can verify exact Hub source snapshot and real preflight (no native binaries on standard runner). Native smoke requires supplied archives and trusted environment; not supplied to GitHub. Foundation App remains unchanged. Rollback revert Hub changes, retain registry. Prior 1.4 main CI 46027674658 PASS.
