# LaunchGuard

A local procurement prototype for Lio's **Keep the Line Running** challenge. Reconciles ERP and physical stock, forecasts completion around shifts and arrivals, and writes deduplicated supplier evidence requests and internal escalations.

## Run

From the parent Lio folder, run `./RUN_LAUNCHGUARD.ps1`. It uses Node from PATH or the bundled runtime already on this computer. Alternatively, use Node.js 22 or later in this directory:

```powershell
node server.mjs
```

Open **http://127.0.0.1:8787**. No npm packages or build step required.

```powershell
node --test test.mjs customer.test.mjs
```

The bundled runtime was found at `$env:USERPROFILE/.cache/codex-runtimes/codex-primary-runtime/dependencies/`. Node v24.22.1 and Python 3.12 are available there even though the shell's aliases are not working. All 16 tests passed using that Node executable.

## Actual Soony archive

The provided `soony-data.zip` is now extracted to the parent `customer-data/soony-data/` directory. Derived records and confidential findings are in this project's ignored `customer-data/` directory. Read **`customer-data/launch-recovery-report.txt` first**.

The app automatically loads `customer-data/snapshot.json` in place of the synthetic startup scenario, with the real BOM, workbook rates and reviewed evidence. It labels this as a **kickoff replay**, not current warehouse truth. `Load Soony archive` restores that snapshot after a sample demonstration.

The pipeline extracts 308 files, normalizes the two PO schemas, preserves raw evidence and source checksums, applies documented review annotations, runs the forecast and writes owner-assigned drafts. Receiving scans are inventoried; decision-relevant scans were visually reviewed, not all images OCR'd.

To refresh after new archive data, with Python and the bundled `pypdf` and `openpyxl` available:

```powershell
python extract_customer_data.py
# Review changed source documents and update customer-data/reviewed-evidence.json.
python normalize_customer_data.py
node analyze_customer.mjs
```

Then select **Load Soony archive** in the app. Review annotations are explicit inputs, not hidden quantities in the forecasting code. Recheck them against changed source checksums before accepting a new drop.

### Live portal sync

From the parent Lio folder, run `./SYNC_SOONY.ps1` and enter the team API token at its hidden prompt. The token is used only in memory as an Authorization Bearer header and is not saved. `./SYNC_SOONY.ps1 -CheckOnly` compares the portal index without downloading customer files. A process-scoped `SOONY_API_TOKEN` environment variable can be used instead of the prompt; do not commit it or pass the token as a command argument.

The command follows the authenticated OpenAPI contract, compares SHA-256 hashes, downloads only new/changed files, and verifies each size and checksum before replacement. Removed portal files are reported and retained locally for audit; do not include them in refreshed analysis. The app reads the private sync status and displays a review warning when changes appear. Synchronization does not claim to revalidate handwritten corrections or legal/quality releases. Refresh the extraction, review annotations and normalized snapshot before relying on changed data. No unattended polling is configured.

## Demo

1. Start with the customer archive banner and inspect the source-linked findings. The real-data analysis and drafts are in `customer-data/`. Select **Load synthetic sample** only for the following illustrative recovery demo.
2. Run the procurement agent. It writes five evidence requests/escalations in the sample and saves the queue and audit trail to `runtime/state.json`.
3. Review the stock mismatch and Collector shortfall. Run again: there are no duplicate drafts.
4. Simulate earlier delivery. The sample shipment becomes confirmed and released at Sunday 14:00. The projected Collector shortfall disappears, but the stock conflict still requires verification.
5. Download the snapshot, change the delivery to Sunday 18:00, import it, and rerun. Even with arrival before dispatch, 1,400 Collector copies cannot be packed in time under these sample rates.
6. Export the work queue. Review messages and use a registered supplier sender outside this application. **The application does not send email or commit spend.**

## Optional local AI

The arithmetic and action planner are explicit deterministic tools, not an LLM. To demonstrate actual AI reading, run a local Ollama model of your choice, then start the server with its installed model name:

```powershell
$env:OLLAMA_MODEL = 'your-installed-model-name'
node server.mjs
```

Paste supplier text and a source name under Evidence workspace. The local `/api/chat` integration translates and extracts claims and exact quotes. Quotes must be found verbatim in the input or extraction is rejected. The result is a **proposal for review**, not a stock adjustment. No cloud model is used. This model integration was not run here because no Ollama model was supplied; enable and verify it before showing AI at work to judges.

## Customer data

The original three PDFs were briefs. The subsequently provided archive supplies the actual stock, shipment, supplier and policy records. It supports an AT RISK assessment under the documented replay assumptions, but does not prove the current physical or released warehouse state.

Future portal synchronization requires the API specification and team token. The authentication scheme must come from that specification; this prototype does not guess it. Customer records and archive-derived reports are ignored by Git. No portal account was created and no credentials were written to disk.

The supplied extraction and normalization scripts handle the actual CSV/Excel/text-PDF archive. Scans and conflicting claims need review annotations. The AI reader accepts text only and does not silently overwrite stock. You can also import another snapshot matching the `sample.json` schema, with source references and `sample: false`.

## Snapshot contract

- `now`: operational snapshot timestamp, not the machine clock. Refresh with each actual drop; `deadline`: dispatch timestamp. Every timestamp must carry a timezone.
- `shifts`: non-overlapping working windows. A shipment's `eta` must mean **usable at the packing line after dock receipt and QA**, not port arrival.
- `products`: independent aggregate line/cell rate per hour, target, packed quantity, BOM `{componentID: unitsPerCopy}` and unique numeric `priority` (lower first). This version assumes dedicated product capacity and fixed material priority, not a globally optimized schedule.
- `components`: `erp` and physically `usable` unallocated units, `observedAt` and `source`. Exclude components already in packed copies. `usable` must include received shipments already counted; the engine ignores lots marked `received`.
- `lots`: unique physical consignment IDs, component, quantity, ETA, source, `updatedAt`, `status` (`confirmed`, `unconfirmed`, `received`) and `released`. Prevent split-message duplicates during normalization.
- `staleHours`: freshness limit (sample assumption: 12 hours). Excludes stale inbound supply; stale stock raises uncertainty and must be verified.

Imports replace the snapshot and reset its work queue; prior audit events remain. New drop normalization must preserve canonical IDs. Queue items remain historical until reviewed/dismissed, even when the forecast improves; the application never asserts that an unanswered request is resolved.

## Architecture and limits

`engine.mjs`: validation, evidence checks, boundary-based packing simulation, prioritized shared stock consumption, action planning. `server.mjs`: loopback HTTP server, serialized mutations, atomic local persistence, optional grounded local model reading. `public/`: responsive UI, safe text rendering, import/export and review workflow. `test.mjs`: numerical and data-integrity regression cases.

This is a **prototype**, not the finished hackathon submission. The actual archive is integrated and assessed. A configured AI model run, stakeholder interviews, supplier responses, a recorded video and deployment remain outstanding. The original generic pitch narrative is in `solution.txt` and `pitch-script.txt`; actual confidential findings are in the ignored customer-data report. No supplier email transport or external messaging is connected.

Local-only operation is deliberate for the supplied folder task and confidential hackathon data. Do not expose this server to a public network; it has no user authentication. Draft/review states do not constitute purchasing authorization.
