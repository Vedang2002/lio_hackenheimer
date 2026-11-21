# LaunchGuard

A local procurement prototype for Lio's **Keep the Line Running** challenge. Reconciles ERP and physical stock, forecasts completion around shifts and arrivals, and writes deduplicated supplier evidence requests and internal escalations.

## Run

Install Node.js 22 or later, then open a terminal in this directory:

```powershell
node server.mjs
```

Open **http://127.0.0.1:8787**. No npm packages or build step required.

```powershell
node --test test.mjs
```

This implementation was exercised through the available Node runtime. The computer's `python` launcher is not functional and Node is not currently on the shell PATH; install Node or use your existing Node executable's absolute path.

## Demo

1. Start with the conspicuous **synthetic demonstration** banner. Only targets, kickoff packed totals and shift windows come from the brief. Component names, stock, rates, priorities and supplier events are invented test inputs.
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

The three supplied PDFs are briefs, not transactional data. No real stock conclusion, supplier identity or commercial approval limit can be derived from them. The actual Soony launch verdict remains unknown.

Register a team in the portal from the brief and obtain the archive and API specification with your team token. The API authentication scheme must be taken from that specification; this prototype does not guess it. Store customer records locally outside Git. Never commit tokens or customer files. No portal credentials were supplied and no account was created.

Normalize actual records to the `sample.json` shape, retain filename/row or page references, set `sample` to `false`, and import the JSON. There is no automatic PDF/OCR/Excel ingestion or live portal sync in this version. CSV/Excel/PDF records require extraction and review first. The AI reader accepts text only. This is an honest integration boundary, not a claim that synthetic fixtures are real customer data.

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

This is a **prototype**, not the finished hackathon submission: real customer-data integration, a verified AI model run, stakeholder interviews, a recorded video, and deployment remain outstanding. Pitch narrative and business case are in `solution.txt` and `pitch-script.txt`. No editable PowerPoint deck is included because the required slide authoring runtime is unavailable. No supplier email transport or external messaging is connected.

Local-only operation is deliberate for the supplied folder task and confidential hackathon data. Do not expose this server to a public network; it has no user authentication. Draft/review states do not constitute purchasing authorization.
