import { useState } from "react";
import type { Case, Overview } from "./api";
import { PackChart } from "./Chart";
import { clock, money, num, when } from "./format";
import { Button, Card, Chip, Label, StateChip } from "./ui";

const HELP_KEY = "foredock.help.dismissed";
const editionName = (name: string | null, id: string) => name?.replace(/^.*? – /, "").replace(/ \(.*/, "") ?? id;

function Kpi({ label, value, hint, tone = "text-ink" }: { label: string; value: string; hint: string; tone?: string }) {
  return (
    <Card className="p-4">
      <Label>{label}</Label>
      <div className={`mt-1 text-3xl font-semibold tabular-nums tracking-tight ${tone}`}>{value}</div>
      <div className="mt-0.5 text-xs text-muted">{hint}</div>
    </Card>
  );
}

function HowTo() {
  const [hidden, setHidden] = useState(() => localStorage.getItem(HELP_KEY) === "1");
  if (hidden)
    return (
      <button className="text-xs text-accent hover:underline" onClick={() => (localStorage.removeItem(HELP_KEY), setHidden(false))}>
        How do I read this page?
      </button>
    );
  const steps = [
    ["1", "Read the headline", "It says in one sentence whether every copy will be packed before the trucks leave."],
    ["2", "Decide the red cards", "Each one is a problem that needs you. Open it, read the suggestion, then approve or change it."],
    ["3", "Leave the rest to the agents", "They read new files, ask suppliers and read the answers. The Activity tab shows what they did."],
  ];
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between">
        <Label>New here? Three things to know</Label>
        <button className="text-xs text-muted hover:text-ink" onClick={() => (localStorage.setItem(HELP_KEY, "1"), setHidden(true))}>
          Got it, hide this
        </button>
      </div>
      <div className="mt-3 grid gap-4 sm:grid-cols-3">
        {steps.map(([n, title, text]) => (
          <div key={n} className="flex gap-3">
            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent text-sm font-semibold text-white">{n}</div>
            <div>
              <div className="text-sm font-medium">{title}</div>
              <div className="text-sm text-muted">{text}</div>
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

function ActionCard({ item, onOpen }: { item: Case; onOpen: () => void }) {
  const rec = item.recommended;
  return (
    <Card className="border-l-4 border-l-bad p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-xs text-muted">{item.description}</div>
          <h3 className="text-lg font-semibold leading-snug">{item.card?.headline ?? item.description ?? item.material_id}</h3>
        </div>
        <StateChip state={item.state} />
      </div>
      <p className="mt-2 text-[15px] leading-relaxed text-ink/90">{item.card?.problem ?? item.summary}</p>
      <div className="mt-3 rounded-lg bg-paper p-3">
        <div className="text-xs font-semibold uppercase tracking-wide text-muted">The agent suggests</div>
        <p className="mt-0.5 text-[15px] leading-relaxed">{item.card?.suggestion ?? rec?.title}</p>
        {rec && rec.copies_saved > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            <Chip tone="good">saves {num(rec.copies_saved)} copies</Chip>
            <Chip>{money(rec.cost, rec.currency)}</Chip>
            {rec.arrives_by && <Chip>on the dock {when(rec.arrives_by)}</Chip>}
          </div>
        )}
      </div>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm">
          <span className="font-semibold">Your step: </span>
          {item.needs_human ? "Decide how to proceed. The agent could not take this further." : (item.card?.your_step ?? "Review and approve.")}
        </p>
        <Button onClick={onOpen}>{item.state === "needs_human" ? "Open and decide" : "Review and approve"}</Button>
      </div>
    </Card>
  );
}

function QuietRow({ item, onOpen }: { item: Case; onOpen: () => void }) {
  const last = [...item.messages].reverse().find((m) => m.status === "sent" || m.status === "received");
  const status =
    item.state === "waiting_for_supplier"
      ? `Asked ${last?.counterpart_name ?? "the supplier"} at ${clock(last?.sent_at)}, waiting for the answer`
      : item.state === "supplier_answered"
        ? `${last?.counterpart_name ?? "The supplier"} answered at ${clock(last?.sent_at)}, the agent is working on the next step`
        : (item.card?.your_step ?? "The agent has a suggestion ready");
  return (
    <button onClick={onOpen} className="flex w-full items-center justify-between gap-4 px-5 py-3.5 text-left hover:bg-paper">
      <div className="min-w-0">
        <div className="font-medium">
          {item.card?.headline ?? item.description} <span className="text-xs font-normal text-muted">· {item.description}</span>
        </div>
        <div className="truncate text-sm text-muted">{status}</div>
      </div>
      <span className="shrink-0 text-sm text-accent">Open ›</span>
    </button>
  );
}

export function Today({ data, onOpen }: { data: Overview; onOpen: (key: string) => void }) {
  const f = data.forecast;
  const short = f.products.filter((p) => p.short > 0);
  const atRisk = short.reduce((sum, p) => sum + p.short, 0);
  const yours = data.cases.filter((c) => c.state === "needs_approval" || c.state === "needs_human");
  const theirs = data.cases.filter((c) => !yours.includes(c));
  const stopOf = (id: string) => f.products.filter((p) => p.first_stop?.material_id === id).map((p) => p.first_stop!.at).sort()[0] ?? "z";
  yours.sort((a, b) => stopOf(a.material_id).localeCompare(stopOf(b.material_id)));
  const partName = new Map(f.components.map((c) => [c.material_id, c.description ?? c.material_id]));

  return (
    <div className="space-y-6">
      <section>
        <h1 className={`text-2xl font-semibold leading-tight tracking-tight sm:text-[32px] ${short.length ? "text-bad" : "text-good"}`}>
          {short.length
            ? `As things stand, ${short.map((p) => `${num(p.short)} ${editionName(p.name, p.product_id)}`).join(" and ")} copies will miss the launch.`
            : "Every copy will be packed before the trucks leave."}
        </h1>
        <p className="mt-2 text-[15px] text-muted">
          {data.erp_forecast.will_make_it && short.length > 0
            ? "Soony's system says both editions finish in time. The delivery notes, inspection reports and supplier mails say otherwise."
            : `Based on the pack-out status of ${when(f.as_of)}.`}
        </p>
      </section>

      <div className="grid gap-4 sm:grid-cols-3">
        <Kpi label="Copies at risk" value={num(atRisk)} hint="will not be packed if nobody acts" tone={atRisk ? "text-bad" : "text-good"} />
        <Kpi
          label="Waiting for you"
          value={String(yours.length)}
          hint={yours.length ? "decisions only a person may take" : "nothing needs you right now"}
          tone={yours.length ? "text-bad" : "text-good"}
        />
        <Kpi
          label="Done by the agents"
          value={String(data.agent_work.sent_by_agents + data.agent_work.replies)}
          hint={`${data.agent_work.sent_by_agents} questions sent, ${data.agent_work.replies} answers read`}
        />
      </div>

      {data.foreign_mail.length > 0 && (
        <Card className="border-l-4 border-l-bad p-4 text-sm">
          <div className="font-semibold text-bad">
            {data.foreign_mail.length} message{data.foreign_mail.length > 1 ? "s" : ""} left this mailbox that the agents did not send
          </div>
          <p className="mt-1 text-muted">
            Last one to {data.foreign_mail[data.foreign_mail.length - 1].counterpart_name} at {clock(data.foreign_mail[data.foreign_mail.length - 1].sent_at)}. Check who
            or what else uses the mailbox: suppliers treat every mail from it as coming from Soony.
          </p>
        </Card>
      )}

      <HowTo />

      <section>
        <h2 className="text-lg font-semibold">What needs you now</h2>
        <div className="mt-3 space-y-4">
          {yours.map((c) => (
            <ActionCard key={c.key} item={c} onOpen={() => onOpen(c.key)} />
          ))}
          {yours.length === 0 && (
            <Card className="p-5 text-sm text-muted">
              {data.cases.length === 0 && data.loop.agents_on ? "The agents are working through the files. Their first suggestions appear here in a few minutes." : "Nothing. The agents will bring a decision here when one is needed."}
            </Card>
          )}
        </div>
      </section>

      {theirs.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold">The agents are handling</h2>
          <Card className="mt-3 divide-y divide-line overflow-hidden">
            {theirs.map((c) => (
              <QuietRow key={c.key} item={c} onOpen={() => onOpen(c.key)} />
            ))}
          </Card>
        </section>
      )}

      {data.cases.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold">What each fix is worth</h2>
          <p className="mt-1 text-sm text-muted">A copy needs every part, so the fixes do not simply add up.</p>
          <Card className="mt-3 divide-y divide-line">
            {[...data.cases]
              .sort((a, b) => b.saves_alone - a.saves_alone)
              .map((c) => (
                <div key={c.key} className="flex flex-wrap items-baseline justify-between gap-x-4 px-5 py-3 text-sm">
                  <span className="font-medium">{c.description}</span>
                  <span className="text-muted">
                    on its own saves <span className="font-semibold text-ink">{num(c.saves_alone)}</span> copies
                    {c.then_limited_by.length > 0 && `, then ${c.then_limited_by.join(" and ")} hold${c.then_limited_by.length > 1 ? "" : "s"} the line`}
                  </span>
                </div>
              ))}
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 px-5 py-3 text-sm">
              <span className="font-semibold">All of them together</span>
              <span className={`font-semibold ${data.plan.short_if_all_solved ? "text-warn" : "text-good"}`}>
                {data.plan.short_if_all_solved == null
                  ? "not known"
                  : data.plan.short_if_all_solved > 0
                    ? `${num(data.plan.short_if_all_solved)} copies still miss the launch`
                    : "every copy is packed in time"}
              </span>
            </div>
          </Card>
        </section>
      )}

      <section>
        <h2 className="text-lg font-semibold">Will we make it?</h2>
        <div className="mt-3 grid gap-4 lg:grid-cols-2">
          {f.products.map((p) => (
            <Card key={p.product_id} className="p-5">
              <div className="flex items-baseline justify-between gap-3">
                <div className="font-semibold">{editionName(p.name, p.product_id)}</div>
                <div className={`text-sm font-semibold ${p.short > 0 ? "text-bad" : "text-good"}`}>
                  {p.short > 0 ? `${num(p.short)} copies short` : `done ${when(p.finished_at)}`}
                </div>
              </div>
              <div className="mt-0.5 text-sm text-muted">
                {num(p.packed)} of {num(p.day_one_demand)} packed · {num(p.to_pack)} to go
              </div>
              <div className="mt-3">
                <PackChart productId={p.product_id} evidence={f} erp={data.erp_forecast} />
              </div>
              {p.first_stop && p.short > 0 && (
                <p className="mt-3 rounded-lg bg-bad-soft px-3 py-2 text-sm text-bad">
                  The line stops {when(p.first_stop.at)} when {partName.get(p.first_stop.material_id) ?? p.first_stop.material_id} runs out.
                </p>
              )}
            </Card>
          ))}
        </div>
      </section>
    </div>
  );
}
