# Hosted application source

For automatic Soony checks, chat model configuration, email bridge credentials and the unattended updater, see [AUTOMATION.md](../AUTOMATION.md). These runtime secrets are not embedded in builds. The current hosted integration suite has eight tests; the reviewed private-seed suite is separate.

This directory contains the GitHub-safe source for LaunchGuard's hosted Worker, storage adapter, embedded UI build and tests. It reuses the core engine, UI and email worker from the main repository instead of copying customer records.

The live Site's approved reviewed snapshot remains in the separate private Site repository. **No reviewed customer seed, raw archive, API token, Gmail password or cloud access credential is included here.**

## Build and test

From the repository root, using Node.js 22 or later:

```powershell
node .\deployment\build.mjs
node --test .\deployment\worker.test.mjs
```

The default build uses the synthetic sample and requires no credentials or private files. Output is `deployment/dist/server/index.js`, with matching `dist/.openai/hosting.json`. Generated outputs are ignored by Git. The six tests use mocked R2 storage and verify forecasts, persistence, deduplication, invalid requests, concurrent-write rejection and downloadable email setup assets.

## Optional approved private build

An authorized operator can supply an existing reviewed snapshot using the process environment variable `LAUNCHGUARD_CUSTOMER_SNAPSHOT`. It is a local JSON file path, not an API token. The build validates and embeds that input into the ignored artifact. Never add customer data or the generated private artifact to GitHub. Publishing such an artifact transmits it to the selected hosting destination and requires authorization for that data and destination.

Without this variable, building this copy does not reproduce or replace the live Site's reviewed customer baseline. Tests here assume the default sample build; the private Site checkout has its own customer-snapshot checks.

## Hosting

The tracked manifest retains the existing Site project ID and declares the R2 binding `BUCKET`. The ID is not an access credential. Reuse the registered Site through its authorized source/version/deployment workflow; do not create a duplicate or bypass Git source provenance. This copy is a source distribution, not automatic GitHub-to-Sites deployment.

The Site is private to its owner and explicitly invited viewers. Authentication and visit access are enforced by Sites dispatch. Re-read the current access policy before publishing; after sharing with viewers, use the shared-site publishing operation rather than the owner-only deployment operation.

The Worker persists application state using R2 conditional ETag writes. An overlapping update returns HTTP 409 instead of overwriting another session. The sample/customer restore operations reset the working queue and restore the deployed baseline. They do not refresh the customer portal or approve procurement decisions.

Viewer is a platform visit role: this application uses a shared work queue and does not implement per-viewer read-only controls for its buttons. Raw document viewing, mailbox activity, local Ollama and direct supplier-email transport are not part of this Worker. The downloadable Gmail worker must be authorized and started separately in Google Apps Script.
