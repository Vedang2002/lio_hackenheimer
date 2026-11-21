# Automatic Gmail reading and replies

**Current hosted deployment:** the user authorized the Desktop OAuth client for support.hackenheimer@gmail.com. The direct hosted worker is enabled on an hourly schedule and updates the dashboard queue without Apps Script. See [direct OAuth setup and verified status](../AUTOMATION.md#direct-hosted-oauth-recommended-with-a-desktop-client). This folder's Apps Script transport is an alternative, not another worker to enable on the same mailbox. The older setup instructions below apply to a fresh Apps Script project, which has not been activated by this change.

## Priority task mode (current default)

`Config.gs` now defaults to **task_queue**. New messages are classified high/medium/normal and the fetched batch is processed in that order. An exact approved rule sends its public reply; other messages get an automatic priority-aware receipt. The sender is verified as **support.hackenheimer@gmail.com**. The timer runs on Google, including with the computer off.

For dashboard-approved answers and queue send-status updates, configure the optional private bridge in [AUTOMATION.md](../AUTOMATION.md#link-inbox-events-to-the-dashboard). It requires a Site secret and two Apps Script Properties. Without the bridge, the Google worker sends priority receipts/configuration rules, but the dashboard does not receive mailbox activity. Bridge errors prevent unconfirmed task answers being sent. Failed status writes retry without another Gmail send. Internal forecast text is never automatically copied into replies.

Google consent and starting the trigger are still necessary. No Google authorization, live send or trigger installation has been performed by this code change.

This cloud worker reads new customer emails and writes threaded Gmail replies or drafts. After you install it in **your Google Apps Script project** and authorize the support account once, Google's timer runs it even when your computer and LaunchGuard browser tab are closed.

**Code is implemented and tested, but no Gmail account has been authorized and no trigger has been installed by this repository. No real emails have been sent.** The Google worker runs independently; the optional authenticated bridge connects dashboard task policies and send status. It never automatically forwards private procurement findings to customers.

## What it does

- Verifies that Google authorized the account configured in `Config.gs`.
- Checks new inbox messages on a one-minute trigger, with persistent scan checkpoints and pagination.
- Supports automatic acknowledgments, approved response rules, and Gmail draft creation.
- Replies in the original conversation using Gmail `threadId`, `In-Reply-To` and `References`.
- Ignores messages from itself, common auto-responders, bulk/list mail, bounces and ambiguous addresses.
- Keeps persistent processing records to avoid automatically sending a duplicate after a timeout or interrupted run.
- Uses a script-wide lock, per-run read limit and daily write cap.
- Does not mark emails read, delete mail, fetch attachments, execute email instructions or send confidential launch data.

Google authorization is handled by Apps Script OAuth. **You do not need to create a separate Google Cloud OAuth client ID/secret or provide a Gmail password.** Google may require API enablement or account/admin approval; a Gmail browser login alone does not authorize this worker.

## One-time setup

1. Sign in to Google as the support account configured in `Config.gs`.
2. Open [Google Apps Script](https://script.google.com/home/start) and create a standalone project named **LaunchGuard Email Automation**.
3. Replace its `Code.gs` with this folder's [Code.gs](Code.gs). Add a script file named **Config** and copy [Config.gs](Config.gs) into it.
4. In **Project Settings**, enable **Show appsscript.json manifest file in editor**. Replace that manifest with [appsscript.json](appsscript.json).
5. Under **Services**, add **Gmail API**, version `v1`, using identifier `Gmail`, if it is not already present. For a standard Google Cloud project, enable Gmail API in that project's API Library as well.
6. Review the reply text, account and mode in `Config.gs`. For a first test, choose `mode: 'draft'` so the worker creates a draft rather than sending it.
7. Select **verifyGmailConnection** from the function dropdown and click **Run**. Complete Google's consent flow yourself. Verify that the execution log names the intended support account. Do not enter the account password into code or this chat.
8. Select **startEmailAutomation** and run it. This installs one time-based trigger and records the activation time. No historical inbox messages from before first activation will be replied to.
9. Send a new test email to the support account from a different address. After a trigger run, inspect Gmail's Drafts and run **showEmailAutomationStatus**. Trigger timing is approximate, not a delivery SLA.
10. To switch to sending, run **stopEmailAutomation**, change `mode` to `'acknowledge'` or `'approved_rules'`, run **acceptEmailConfiguration**, then run **startEmailAutomation** again. Review the configured text before enabling automatic sending.

There is no web-app deployment, API key, Apps Script web endpoint, ChatGPT connector or personal computer process required for this timer. Apps Script installs and executes the trigger under the authorizing Google account. An account policy or OAuth restriction may require administrator action; this code does not bypass it.

## Reply modes

| Mode | Behavior |
| --- | --- |
| `task_queue` | Default: ranks fetched messages by priority and sends an approved answer or priority receipt. Optional private bridge links dashboard task policies and send status. |
| `acknowledge` | Sends the exact configured acknowledgment to eligible new messages. It confirms receipt only, not that a request was reviewed or resolved. |
| `approved_rules` | Sends an approved rule's exact response only when exactly one rule matches. Unknown or ambiguous questions are held for review. |
| `draft` | Creates a draft using a matching approved response or the acknowledgment. It does not send. |

This version does **not** invent free-form AI answers. Literal templates and approved public answers keep arbitrary email text from authorizing business commitments. Add an AI drafting step separately if needed; do not silently treat an untrusted email as an instruction to disclose data or execute actions.

### Configure approved responses

Edit the `rules` array in `Config.gs`. For example:

```javascript
mode: 'approved_rules',
rules: [
  {
    id: 'public-contact-process',
    matchAny: ['how do i contact your team', 'contact your support team'],
    reply: 'You can contact Hackenheimer Support by replying to this email.'
  }
]
```

Rules match case-insensitive phrases in the subject and available plain-text message body, excluding common quoted reply history. They are simple phrase matches, not semantic understanding. Write narrow phrases and test them. Messages matching multiple rules are not auto-sent. HTML-only emails may have no body text for matching and can require review. The worker replies only to a single parsed Reply-To/From address, never reply-all.

Stop the worker before editing approved settings. Changed configuration is rejected until **acceptEmailConfiguration** is run while stopped. Keep total configuration below 7,000 characters. Larger knowledge bases need a different persistence design.

## Operating controls

| Function | Purpose |
| --- | --- |
| `verifyGmailConnection` | Verify the OAuth account and configuration without sending mail. |
| `startEmailAutomation` | Enable processing and create the timer if absent. Repeated starts do not create another trigger. |
| `stopEmailAutomation` | Disable processing and remove this worker's triggers; retain email and processing records. |
| `processCustomerInbox` | Run one batch manually or through the timer. It does nothing while disabled. |
| `showEmailAutomationStatus` | Log enabled state, latest run/error and processing-state counts. |
| `listEmailRecordsForReview` | List up to 100 held/uncertain incoming Gmail IDs and reasons. |
| `reconcileUncertainEmailWrites` | Look for matching Message-IDs in Sent/Drafts. It never resends. |
| `pruneOldEmailProcessingRecords` | Remove terminal processing metadata older than 30 days and behind a completed scan; retain held/uncertain records. It does not delete emails. |

Default limits are 20 newly fetched messages per run, five listing pages per run, and 50 attempted sends/draft creations per UTC day. Google service quotas also apply. Remaining pages and unfinished pages resume in later runs without advancing the completed scan checkpoint. A five-minute query overlap reduces boundary misses; processing records deduplicate overlapping results. Resume after stopping processes messages since the last completed scan, but not before the original activation time.

## Handling uncertain outcomes

Before calling Gmail, the worker records a send/draft reservation and consumes its write budget. If Gmail accepts the message but the response is lost, the next run does not blindly send it again. The record remains uncertain until reconciliation finds the deterministic Message-ID in Sent/Drafts or a person investigates.

This favors avoiding duplicates over guaranteeing delivery after every failure. A connection failure before transmission can also require manual review. **Do not erase uncertain records or re-run old messages without inspecting Gmail first.** Google is not providing an exactly-once send API through this implementation.

Processing state lives in Script Properties. Do not share this Apps Script project publicly or run a second independent copy against the same mailbox: their locks and processing records are independent. Property-storage and execution quotas are finite; monitor status and prune old terminal metadata periodically. If a saved Gmail page token expires, stop, accept the configuration again to clear the partial scan, and restart; the last completed checkpoint and processing records are retained.

## Data and authorization

The code requests `gmail.readonly` to read messages and `gmail.compose` to create drafts and send messages, plus Apps Script permissions for API calls and trigger management. It does not request the ability to modify inbox labels or delete received mail. Email content is processed inside the Google account's Apps Script project and is not copied to GitHub or the LaunchGuard Site. Script Properties retain configuration, scan state and small per-message status records, not message bodies or passwords.

This repository contains no real Google OAuth credentials. The password previously shared in conversation is not used. Routine replies become active only after the account owner completes authorization and runs the start function.

## Tests

From the repository root:

```powershell
node --test .\email-automation\email.test.mjs
```

The tests execute the actual Apps Script code in a Node sandbox with mocked Google services. They cover identity checks, automatic replies, Gmail drafts, threading, duplicate suppression, loop prevention, rule selection, uncertain sends, limits, settings changes, locks, MIME encoding and batch resumption. They **do not** prove OAuth consent, trigger execution or live Gmail delivery; complete the end-to-end test above before relying on the worker.

## References

- [Google Apps Script installable triggers](https://developers.google.com/apps-script/guides/triggers/installable)
- [ScriptApp OAuth token](https://developers.google.com/apps-script/reference/script/script-app#getOAuthToken())
- [Gmail messages list](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list) and [send](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send)
- [Gmail threading requirements](https://developers.google.com/workspace/gmail/api/guides/threads#adding_drafts_and_messages_to_threads)
- [Apps Script quotas](https://developers.google.com/apps-script/guides/services/quotas)
