# Private repository access — owner setup

No GitHub App or credentials are created by Hub code. Existing ChatGPT connector access is not a workflow credential. The optional manual workflow foundation-private-smoke.yml is restricted to main and actor Jugadru; no PR, schedule, arbitrary repo or SHA input. Uses pinned official GitHub App token action, repository Contents read only, token scoped to APEX-Foundation and revoked by the action at job end. Checkout does not persist credentials. No write permission or cross-private automatic execution. Never use a personal access token or share a private key in chat.

Owner setup:
1. Register a private GitHub App at https://github.com/settings/apps/new, e.g. APEX Hub Read Access. Disable webhook (no callback required); only this account may install. Repository permissions: Contents Read-only, Metadata Read-only (automatic). No other write permissions.
2. Install the App on Jugadru; select only APEX-Foundation for this initial smoke. Generate a private key locally.
3. In private Hub repository Settings → Environments create apex-private-read, restrict deployment branches to main (if available), configure required approval if supported. Add environment variable APEX_READ_APP_CLIENT_ID with App Client ID and environment secret APEX_READ_APP_PRIVATE_KEY with PEM contents. If branch/environment protections are not available, do not configure the credential until owner reviews the risk and chooses a supported protected setup. Do not commit keys. Keep secret restricted to this environment.
4. Once merged, manually run Foundation private consumer smoke from main. Missing credentials fail explicitly. Inspect actual PASS and exact source SHA; no evidence from skipped workflow or failed checkout is accepted. Revoke or rotate key if compromised. Uninstall App or remove environment secret to disable access.

GitHub docs: https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/making-authenticated-api-requests-with-a-github-app-in-a-github-actions-workflow
Scope: read Foundation only; future repository access, Actions read/dispatch or writes are separate owner decisions. Real app path remains NOT_RUN until configuration and successful manual execution. Private artifacts remain in private Hub; no upload to public game.
