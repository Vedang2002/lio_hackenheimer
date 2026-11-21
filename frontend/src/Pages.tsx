import type { ComponentForecast, Overview } from "./api";
import { clock, fileName, num } from "./format";
import { Card, Chip, Label } from "./ui";

function Coverage({ c }: { c: ComponentForecast }) {
  const scale = Math.max(c.need, c.usable_stock + c.inbound_counted) || 1;
  const pct = (n: number) => `${(100 * n) / scale}%`;
  return (
    <div className="relative h-3 w-full overflow-hidden rounded-full bg-bad-soft">
      <div className="absolute inset-y-0 left-0 bg-good" style={{ width: pct(Math.min(c.usable_stock, scale)) }} />
      <div className="absolute inset-y-0 bg-good/45" style={{ left: pct(c.usable_stock), width: pct(c.inbound_counted) }} />
      <div className="absolute inset-y-0 w-0.5 bg-ink" style={{ left: `calc(${pct(c.need)} - 1px)` }} title={`Needed: ${num(c.need)}`} />
    </div>
  );
}

export function Parts({ data, onOpen }: { data: Overview; onOpen: (key: string) => void }) {
  const cases = new Set(data.cases.map((c) => c.material_id));
  const rows = [...data.forecast.components].sort((a, b) => b.gap - a.gap || Number(b.erp_stock !== b.usable_stock) - Number(a.erp_stock !== a.usable_stock));
  const quiet = data.findings.filter((f) => f.level !== "stops_line");
  return (
    <div className="space-y-6">
      <section>
        <h1 className="text-2xl font-semibold tracking-tight">Parts for the copies still to pack</h1>
        <p className="mt-1 text-[15px] text-muted">
          Each bar compares what is needed (the black mark) with what is really there. Green is on site, light green is on its way, red is missing.
        </p>
      </section>
      <Card className="divide-y divide-line">
        {rows.map((c) => {
          const wrong = Math.round(c.erp_stock) !== Math.round(c.usable_stock);
          return (
            <div key={c.material_id} className="grid items-center gap-x-6 gap-y-2 px-5 py-4 sm:grid-cols-[minmax(0,1.2fr)_minmax(0,1.5fr)_auto]">
              <div className="min-w-0">
                <div className="font-medium">{c.description}</div>
                <div className="text-xs text-muted">{c.material_id}</div>
              </div>
              <div>
                <Coverage c={c} />
                <div className="mt-1.5 flex flex-wrap gap-x-4 text-xs text-muted">
                  <span>needed {num(c.need)}</span>
                  <span>on site {num(c.usable_stock)}</span>
                  {c.inbound_counted > 0 && <span>on its way {num(c.inbound_counted)}</span>}
                  {wrong && <span className="text-bad">the system shows {num(c.erp_stock)}</span>}
                  {c.inbound_needing_action > 0 && <span className="text-warn">{num(c.inbound_needing_action)} more if someone acts</span>}
                </div>
              </div>
              <div className="flex items-center gap-2 sm:justify-end">
                {c.gap > 0 ? <Chip tone="bad">{num(c.gap)} missing</Chip> : wrong ? <Chip tone="warn">enough, but the system is wrong</Chip> : <Chip tone="good">enough</Chip>}
                {cases.has(c.material_id) && (
                  <button className="text-sm text-accent hover:underline" onClick={() => onOpen(`part:${c.material_id}`)}>
                    Open ›
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </Card>
      {quiet.length > 0 && (
        <Card className="p-5">
          <Label>Also noticed · not stopping a line</Label>
          <ul className="mt-3 space-y-2 text-sm">
            {quiet.map((f) => (
              <li key={f.key}>
                <div>{f.title}</div>
                <div className="text-muted">{f.detail}</div>
              </li>
            ))}
          </ul>
        </Card>
      )}
      <p className="text-xs text-muted">How this is counted: {data.forecast.assumptions.join(" ")}</p>
    </div>
  );
}

const KINDS: Record<Overview["activity"][number]["kind"], { label: string; dot: string }> = {
  files: { label: "New files", dot: "bg-accent" },
  reply: { label: "Supplier answered", dot: "bg-good" },
  sent_by_agent: { label: "Agent asked", dot: "bg-ink/60" },
  sent_by_human: { label: "You approved", dot: "bg-warn" },
  handover: { label: "Handed to you", dot: "bg-bad" },
};

export function Activity({ data, onOpen }: { data: Overview; onOpen: (key: string) => void }) {
  return (
    <div className="space-y-6">
      <section>
        <h1 className="text-2xl font-semibold tracking-tight">What happened</h1>
        <p className="mt-1 text-[15px] text-muted">Everything the agents noticed and did, newest first. Times are Hamburg time.</p>
      </section>
      <Card className="p-5">
        <ol className="relative space-y-5 border-l border-line pl-6">
          {data.activity.map((a, i) => (
            <li key={i} className="relative">
              <span className={`absolute -left-[29px] top-1.5 h-2.5 w-2.5 rounded-full ${KINDS[a.kind].dot}`} />
              <div className="flex flex-wrap items-baseline gap-x-2 text-xs text-muted">
                <span className="tabular-nums">{clock(a.at)}</span>
                <span>· {KINDS[a.kind].label}</span>
              </div>
              <div className="text-[15px]">
                {a.text}
                {a.case_key && (
                  <button className="ml-2 text-sm text-accent hover:underline" onClick={() => onOpen(a.case_key!)}>
                    Open ›
                  </button>
                )}
              </div>
              {a.detail && <div className="text-sm text-muted">{a.detail}</div>}
            </li>
          ))}
          {data.activity.length === 0 && <li className="text-sm text-muted">Nothing yet.</li>}
        </ol>
      </Card>
    </div>
  );
}

export function Agents({ data }: { data: Overview }) {
  const top = Math.max(...data.spend.by_kind.map((k) => k.usd), 0.01);
  const rules: [string, string][] = [
    ["Send on their own", "Questions, reminders and requests for confirmation. Each one is checked twice before it leaves and goes only to a supplier contact from Soony's own data."],
    ["Wait for a person", "Anything that places, releases or changes an order, or accepts a price or a condition. That is one click for the person Soony's approval policy names."],
    ["Hand over", "A case with no route left, or one the agent failed to work twice. It stops there and tells you why."],
    ["Stop by themselves", `After three unattended messages to one contact, and when the day's budget of $${data.spend.budget_usd.toFixed(0)} is used up.`],
  ];
  return (
    <div className="space-y-6">
      <section>
        <h1 className="text-2xl font-semibold tracking-tight">The agents</h1>
        <p className="mt-1 text-[15px] text-muted">What they have done today, what it cost, and what they are allowed to do without you.</p>
      </section>
      <div className="grid gap-4 sm:grid-cols-4">
        {[
          ["Files watched", num(data.files), `${num(data.documents_read)} documents read`],
          ["Problems found", String(data.agent_work.findings), "that the system did not show"],
          ["Messages", String(data.agent_work.sent_by_agents + data.agent_work.approved_by_people), `${data.agent_work.sent_by_agents} by agents, ${data.agent_work.approved_by_people} approved by you`],
          ["Cost today", `$${data.spend.today_usd.toFixed(2)}`, `limit $${data.spend.budget_usd.toFixed(0)} a day`],
        ].map(([label, value, hint]) => (
          <Card key={label} className="p-4">
            <Label>{label}</Label>
            <div className="mt-1 text-3xl font-semibold tabular-nums tracking-tight">{value}</div>
            <div className="mt-0.5 text-xs text-muted">{hint}</div>
          </Card>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-5">
          <Label>Where the money went</Label>
          <div className="mt-4 space-y-3">
            {data.spend.by_kind.map((k) => (
              <div key={k.kind}>
                <div className="flex justify-between text-sm">
                  <span className="first-letter:uppercase">{k.kind}</span>
                  <span className="tabular-nums text-muted">
                    ${k.usd.toFixed(2)} · {k.calls} calls
                  </span>
                </div>
                <div className="mt-1 h-2 rounded-full bg-line">
                  <div className="h-2 rounded-full bg-accent" style={{ width: `${(100 * k.usd) / top}%` }} />
                </div>
              </div>
            ))}
          </div>
        </Card>
        <Card className="p-5">
          <Label>What they may and may not do</Label>
          <dl className="mt-3 space-y-3 text-sm">
            {rules.map(([title, text]) => (
              <div key={title}>
                <dt className="font-medium">{title}</dt>
                <dd className="text-muted">{text}</dd>
              </div>
            ))}
          </dl>
        </Card>
      </div>
      <Card className="p-5">
        <Label>Record of who did what</Label>
        <ul className="mt-3 space-y-2 text-sm">
          {data.audit.map((a, i) => (
            <li key={i}>
              <span className="tabular-nums text-muted">{clock(a.ts)}</span> <span className="font-medium">{a.actor}</span> {a.action}
              {a.detail && <span className="text-muted"> · {a.detail}</span>}
            </li>
          ))}
          {data.audit.length === 0 && <li className="text-muted">Nothing recorded yet. Approvals, refusals, automatic sends and handovers appear here.</li>}
        </ul>
      </Card>
      <Card className="p-5 text-sm">
        <Label>Status</Label>
        <p className="mt-2">
          {data.loop.agents_on ? (data.loop.running ? "The agents are working right now." : "The agents are on and check the portal and the mailbox every two minutes.") : "The agents are switched off on this copy of the desk; it only shows what is stored."}
          {data.last_sync && ` Last check of the portal: ${clock(data.last_sync.started_at)}.`}
          {data.mail_address && ` Mailbox: ${data.mail_address}.`}
        </p>
        {data.loop.last_tick?.errors?.length ? <p className="mt-2 text-bad">Last pass reported: {data.loop.last_tick.errors.join("; ")}</p> : null}
        {data.needs_review.length > 0 && (
          <div className="mt-3">
            <div className="font-medium">Documents the agents were not sure they read correctly</div>
            <ul className="mt-1 text-muted">
              {data.needs_review.map((r) => (
                <li key={r.path}>
                  {fileName(r.path)}: {r.detail}
                </li>
              ))}
            </ul>
          </div>
        )}
      </Card>
    </div>
  );
}
