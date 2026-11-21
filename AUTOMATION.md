# LaunchGuard automatic operations

## What runs automatically

- **Hosted Soony and Gmail checks:** an enabled linked cloud task calls `POST /api/automation` **hourly**. This hosting scheduler permits at most one run per hour; a five-minute request was rejected. It runs with the page and computer closed. The Site's `SOONY_API_TOKEN` and Gmail OAuth credentials are secrets; the browser never receives them. The local Node server checks Soony every five minutes while running. The Apps Script alternative can check email every minute.
- **Evidence downloads:** the current file index is validated, each changed file is downloaded from the fixed Soony host and checked against its declared size and SHA-256. At most ten changed files / 20 MB are downloaded per run, with later runs continuing. Data stays in private R2 storage (local: ignored `runtime/soony/`).
- **Task planning:** forecast findings become deduplicated, owner-assigned tasks, ranked high/medium/normal. New file versions produce high-priority evidence-review tasks. Reviewed decisions are retained; disappeared forecast findings become inactive. The page refreshes every 30 seconds without a Sync button.
- **Customer inbox:** after one-time Google authorization, Apps Script polls new inbox messages every minute and replies through **support.hackenheimer@gmail.com**. It prioritizes the bounded fetched batch, records sends before transmission, and never blindly retries an uncertain send. Gmail itself receives messages; the timer reads them.

Automatic file synchronization does **not** automatically verify changed legal releases, inspect images, or turn incoming shipments into received stock. The forecast retains the last reviewed normalized snapshot until changed evidence is reviewed and imported. The dashboard clearly reports that distinction. The Soony API currently exposes files only, not tasks, CRM contacts or mail.

## Chatbot

Ask LaunchGuard about risks, next priorities, components, sync or email status. The default evidence assistant retrieves deterministic answers and displays source references. It does not claim to use an AI model when none is configured. It is read-only and has no mail or stock mutation tools.

**Free providers:** local Ollama has no API fee; set `OLLAMA_MODEL` and run Ollama on `127.0.0.1:11434`. For a hosted chatbot, use a **Gemini API free-tier project**, set `GEMINI_API_KEY` as a secret and `GEMINI_MODEL` to a text model currently available on that project's free tier. Create the key yourself in [Google AI Studio](https://aistudio.google.com/api-keys); do not enable paid billing for this setup. Free access has quota and regional/account eligibility limits. A free Gemini website account does not supply an API key to this application.

Set `AI_DATA_SHARING=enabled` only after approving the provider's data terms and sharing the question plus bounded retrieved evidence with it. Google's [pricing and data-use table](https://ai.google.dev/gemini-api/docs/pricing) states that free-tier content may be used to improve its products; check the terms applicable to your location and account before using confidential evidence. Until approval and a key are configured, the chatbot uses its local evidence answers. This change neither creates a paid account nor falls back to a paid provider. On quota or provider errors, it shows the evidence answer. Keys never go to the browser or Git.

The implementation uses Gemini's [generateContent API](https://ai.google.dev/api/generate-content). Configure the key through secure hosting settings; never paste it into source, chat, a browser bundle or Git. No paid-provider fallback is implemented. AI prose still needs verification against source evidence.

## Connect Gmail once

### Direct hosted OAuth (recommended with a Desktop client)

The hosted worker can connect directly to Gmail using the supplied Desktop OAuth client, avoiding Apps Script and bridge credentials. It uses Google's free Gmail API; the account's service quotas apply. It shares the same task policies and persistent queue as the dashboard. Only the chatbot needs a separate AI-provider key.

1. In the OAuth client's Google Cloud project, enable **Gmail API**. Configure the OAuth consent screen and, for an app in Testing, add **support.hackenheimer@gmail.com** as a test user.
2. Run from the repository root:

   ```powershell
   node email-automation/connect-gmail.mjs "C:\path\to\client_secret_desktop.json"
   ```

3. Open the printed Google authorization URL yourself. It requests `gmail.readonly` and `gmail.send`, uses PKCE and a random state, and returns to the local callback. The helper only accepts the support account. No screen or browser automation is needed; no mail is sent during setup.
4. After successful consent, the helper writes a temporary credential file at ignored `launchguard/runtime/gmail-oauth.json`. It refuses to overwrite a previous grant. Treat this as a secret; exclude it from cloud-sync/backups as appropriate. Securely transfer its `client_id`, `client_secret` and `refresh_token` into Site secret variables **GMAIL_CLIENT_ID**, **GMAIL_CLIENT_SECRET** and **GMAIL_REFRESH_TOKEN**, set **GMAIL_ENABLED=true**, and deploy. Remove the temporary local grant after verified secret transfer; keep the original Google client file outside Git.
5. The linked hourly updater verifies the account, polls new mail, prioritizes a fetched batch and automatically replies from the support account. The first enabled run establishes the activation boundary; older mail is excluded. It writes the reply status directly to the agent queue. No Apps Script project, user-managed Site service token or email bridge secret is needed for this mode. The linked cloud task obtains its own supported Site service access each run.
6. Send a fresh test email from a second address after activation. Verify Gmail Sent and the dashboard's Customer emails view. Until the OAuth grant, runtime secrets and first verified run are complete, the dashboard must not claim Connected. Set **GMAIL_ENABLED=false** and deploy to pause it. Revoke the app in Google account security to revoke access entirely.

The direct worker caps reads at 20 messages/run and sends at 50/day, keeps bounded page checkpoints, and uses an R2 conditional-write lease. Each send is durably reserved before calling Gmail. A timeout remains uncertain and is only reconciled against Sent in later runs, never automatically resent. No inbox labels are changed or messages deleted. OAuth access tokens stay in memory. Refresh credentials stay in Site secrets, outside R2 and source. Enable only one transport for this mailbox; stop any Apps Script worker before switching.

Google may block an unverified app, require administrator approval, or expire refresh tokens for an external app in Testing (commonly after seven days for Gmail scopes). Complete Google's appropriate publishing/verification process for continued production use. A client JSON alone does not authorize mailbox access. See [Google desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app).

**Verified deployment on 21 November 2026:** the supplied Desktop client was authorized by the user, the support account was verified, and the offline grant was stored as Site secrets. The temporary local grant was removed after a successful live refresh. The first enabled run succeeded at approximately 18:26 CET, reading 0 eligible messages and sending 0 replies. The hosted Soony check verified an index of 318 files and downloaded all 11 changed files in two bounded runs; these remain evidence-review tasks, not automatic inventory changes. The hourly task is enabled; its first scheduled occurrence was saved for 19:27 CET. This proves manual service-path execution and schedule creation, not a completed scheduled run or end-to-end customer delivery. Send a new test message from another account after activation and verify its reply.

### Alternative: Google Apps Script

Follow [email-automation/README.md](email-automation/README.md) or the hosted `/email-setup.html`. Install the supplied files in an Apps Script project belonging to **support.hackenheimer@gmail.com**, run `verifyGmailConnection`, complete Google's consent yourself, then run `startEmailAutomation`. No browser or screen access by the coding agent is necessary. Code deployment alone does not grant Google authorization.

The default `task_queue` mode classifies new messages and automatically sends a priority-aware receipt or one approved configuration rule. It does not invent delivery commitments. Unknown or ambiguous requests receive only a receipt. Only messages received after first activation are eligible. The worker is usable independently of the dashboard.

### Link inbox events to the dashboard

To make **dashboard task answers** and confirmed email statuses available to the Gmail worker, configure the private bridge:

1. Generate a strong random `EMAIL_BRIDGE_SECRET` in a secure local shell or secret manager. Store it as a secret Site runtime variable, and redeploy so it takes effect. Do not commit it.
2. In Apps Script **Project Settings → Script Properties**, set `LG_EMAIL_BRIDGE_SECRET` to that same value and `LG_SITE_SERVICE_TOKEN` to the Site's supported service-access token. Obtain that token through the Site owner’s supported service-access controls; request a secure setup flow if needed. Never change the Site to public to make this work. Service access bypasses the sign-in gate and must be kept private.
3. Keep `siteUrl` in `Config.gs` set to the registered LaunchGuard Site. Stop the worker, run `acceptEmailConfiguration`, then restart. A heartbeat should appear in the dashboard within a few trigger runs.
4. Expand a procurement task's **Approve a customer-facing answer** section. Enter the **exact recipient**, a reference expected in their message (such as a PO identifier), and an approved public answer. Save it. Exactly one active matching policy is required; otherwise the worker sends a receipt. Deleting/dismissing a policy task stops it matching. Email text cannot grant itself authority or ask the assistant to reveal internal records.
5. Send a new test message from the configured recipient containing that reference. Verify Gmail Sent and the dashboard's Customer emails view. An uncertain write remains flagged for manual reconciliation. After Google authorization, actual delivery still requires this end-to-end check.

The bridge uses both platform service access and its own narrow secret. Credentials stay in Apps Script Properties and Site secrets. It stores sender, subject, priority, Gmail IDs, the outgoing reply and send status in the private queue; it does not retain the incoming body. Processing sends the incoming subject/body to the same private Site for matching. The Google project’s owner can inspect its processing metadata. Invited Site visitors share the app queue, including this email metadata; the platform visit role does not make the app's controls read-only.

If a configured bridge fails, the worker waits rather than sending an unconfirmed dashboard answer. Failed status writes retry separately without repeating the Gmail send. A copied independent Apps Script project has separate duplicate records: run only one active worker per mailbox. Secrets and Google authorization are not embedded in the downloadable files.

## Verify and operate

```powershell
node --test launchguard/test.mjs launchguard/customer.test.mjs launchguard/automation.test.mjs email-automation/email.test.mjs
node --test launchguard/gmail.test.mjs
node deployment/build.mjs
node --test deployment/worker.test.mjs
```

The tests mock external mail/provider/Soony services and exercise file tampering, failures, queue state, exact-recipient rules, priority ordering and duplicate protection. They do not prove live OAuth or delivery. No real customer emails are sent by the tests.

For the hosted updater, a scheduled run must obtain fresh Site service access through `get_site`, POST `{}` as JSON to `/api/automation` with `OAI-Sites-Authorization`, and read `/api/state` back with the same access. Do not put tokens in the task prompt or source. Retry a 409 using a fresh request; R2 conditional writes prevent lost changes. A failed Soony request marks the source stale and retains the previous forecast. Do not redeploy on routine updates or change the Site's audience. Pause/remove the linked cloud task to stop hosted checks; stop Node to stop local checks.
