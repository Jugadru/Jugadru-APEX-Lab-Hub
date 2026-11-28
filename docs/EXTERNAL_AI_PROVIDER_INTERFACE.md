# External AI Provider Interface — 3.1.0

Base: integrated 3.0.1 commit 4404953445e664f58927762fe13fc77f7f32723e. One capability: offline provider descriptors and exact fail-closed selection for existing advisory context transport. No provider API, network transport, external host installation, credential discovery, paid calls or automatic execution.

The version-1 descriptor lists provider ID, reviewed transport, model identity status, allowed goals, activation requirement, execution authority, and Hub call/cost limits. manual_ai and mcp_stdio remain operator-approved context transports, not authenticated models. chatgpt, codex, local_ai, remote_mcp, openai_api and other_api are unavailable. Unknown providers and unsupported goals are rejected; there is no default or fallback. Catalog results are fresh copies. Existing request schema, exact approval digest, one-shot release, expiry and advisory recording semantics stay unchanged. Selection is enforced at both proposal and release. No historical data migration.

Usage: python3 -m hub.ai_gateway --registry PATH catalog
Inspect provider_interface in output. Follow AUTHORIZED_AI_MCP.md for existing approvals. No new CLI network or execution command. External host costs are not enforced or measured by Hub; zero Hub calls does not guarantee zero provider cost. Local operator trust is not agent authentication or a sandbox. No raw evidence, private Architect data, credentials or new external transfer authorized.

Acceptance: provider catalog immutability, exact selection, deny disabled and unknown providers, deny transport substitution, preserve one-shot schema-1 exchange; existing actual MCP smoke and all integrated CI gates. No live external AI claimed. Rollback: revert candidate commit; existing registries remain compatible. Separate follow-up: ChatGPT Context Bridge, Codex Development Bridge, local AI bridge, each requiring reviewed host setup and specific operator approval.
