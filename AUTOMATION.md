# Automatic operations

## Email access removed

At the user's request, LaunchGuard no longer has Gmail access. Mailbox credentials and authorization helpers are removed, all email API routes return HTTP 410, and the website does not read or send emails. Gmail account sign-in and the password are unchanged. Historical Git revisions contain no OAuth grant and cannot restore access after revocation.

## Soony sync and task planning

The hourly cloud task may still invoke /api/automation for Soony file checks and task prioritization. The endpoint contains no mail worker. It checks source file hashes, verifies changed-file downloads, and creates review tasks while retaining the last reviewed forecast. The local server checks Soony every five minutes when SOONY_API_TOKEN is configured.

## Chat assistant

Evidence answers remain available. Optional local Ollama or free-tier Gemini can explain bounded evidence. Gemini requires a secret GEMINI_API_KEY, an eligible GEMINI_MODEL and explicit AI_DATA_SHARING=enabled after reviewing provider data terms. No Gmail access is supplied to the assistant.

## Verification

Run node deployment/build.mjs, then node --test launchguard/test.mjs launchguard/customer.test.mjs launchguard/automation.test.mjs deployment/worker.test.mjs. The regression suite verifies that removed email endpoints stay disabled even if obsolete credentials are supplied.
