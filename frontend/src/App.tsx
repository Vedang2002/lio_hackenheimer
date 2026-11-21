import { useCallback, useEffect, useState } from "react";
import { getOverview, setAccessKey, Unauthorized, type Overview } from "./api";
import { CasePanel } from "./CasePanel";
import { countdown, hoursBetween, when } from "./format";
import { Activity, Agents, Parts } from "./Pages";
import { Today } from "./Today";
import { Button, Card } from "./ui";

const REFRESH_MS = 20_000;
type Page = "today" | "parts" | "activity" | "agents";
const PAGES: { id: Page; label: string }[] = [
  { id: "today", label: "Today" },
  { id: "parts", label: "Parts" },
  { id: "activity", label: "Activity" },
  { id: "agents", label: "Agents" },
];

function Gate({ onDone }: { onDone: () => void }) {
  const [key, setKey] = useState("");
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <Card className="w-full max-w-sm p-6">
        <div className="text-xl font-semibold tracking-tight">Foredock</div>
        <p className="mt-1 text-sm text-muted">This desk shows Soony's data. Enter your team's access key.</p>
        <form
          className="mt-4 space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            setAccessKey(key.trim());
            onDone();
          }}
        >
          <input
            autoFocus
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="Access key"
            className="w-full rounded-lg border border-line px-3 py-2 text-sm"
          />
          <Button disabled={!key.trim()}>Open the desk</Button>
        </form>
      </Card>
    </div>
  );
}

export default function App() {
  const [data, setData] = useState<Overview | null>(null);
  const [locked, setLocked] = useState(false);
  const [error, setError] = useState("");
  // The page and the open case live in the address, so either can be shared as a link.
  const [page, setPageState] = useState<Page>(() => {
    const asked = new URLSearchParams(location.search).get("page") as Page | null;
    return PAGES.some((p) => p.id === asked) ? asked! : "today";
  });
  const [open, setOpenState] = useState<string | null>(() => decodeURIComponent(location.hash.slice(1)) || null);
  const address = (p: Page, key: string | null) =>
    history.replaceState(null, "", `${location.pathname}${p === "today" ? "" : `?page=${p}`}${key ? `#${encodeURIComponent(key)}` : ""}`);
  const setPage = (p: Page) => {
    setPageState(p);
    address(p, open);
    window.scrollTo(0, 0);
  };
  const setOpen = (key: string | null) => {
    setOpenState(key);
    address(page, key);
  };
  const [tick, setTick] = useState(0);

  const load = useCallback(async () => {
    try {
      setData(await getOverview());
      setLocked(false);
      setError("");
    } catch (e) {
      if (e instanceof Unauthorized) setLocked(true);
      else setError(e instanceof Error ? e.message : "The desk cannot reach its server.");
    }
  }, []);

  useEffect(() => {
    load();
    const refresh = setInterval(load, REFRESH_MS);
    const clock = setInterval(() => setTick((t) => t + 1), 30_000);
    return () => {
      clearInterval(refresh);
      clearInterval(clock);
    };
  }, [load]);

  if (locked) return <Gate onDone={load} />;
  if (!data)
    return <div className="flex min-h-screen items-center justify-center text-sm text-muted">{error || "Opening the desk…"}</div>;

  const f = data.forecast;
  if (!f.ready)
    return (
      <div className="flex min-h-screen items-center justify-center p-6 text-center text-sm text-muted">
        <div>
          <div className="text-base font-medium text-ink">Reading Soony's files…</div>
          <p className="mt-1">
            {data.files} files found, {data.documents_read} documents read so far. {f.reason}
          </p>
        </div>
      </div>
    );

  const hoursLeft = hoursBetween(data.now, f.deadline) - (tick * 0.5) / 60;
  const waiting = data.cases.filter((c) => c.state === "needs_approval" || c.state === "needs_human").length;
  const opened = data.cases.find((c) => c.key === open);

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-line bg-paper/95 backdrop-blur">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-x-6 gap-y-2 px-5 py-3">
          <div className="flex items-baseline gap-3">
            <div className="text-xl font-semibold tracking-tight">Foredock</div>
            <div className="hidden text-sm text-muted sm:block">
              Soony · Grand Heist 6 launch · {data.me.name ?? "shared access"}
              {data.me.role === "viewer" && " (view only)"}
            </div>
          </div>
          <nav className="order-3 flex w-full gap-1 sm:order-none sm:w-auto">
            {PAGES.map((p) => (
              <button
                key={p.id}
                onClick={() => setPage(p.id)}
                className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${page === p.id ? "bg-ink text-white" : "text-muted hover:bg-line/60 hover:text-ink"}`}
              >
                {p.label}
                {p.id === "today" && waiting > 0 && (
                  <span className={`ml-1.5 rounded-full px-1.5 text-xs ${page === p.id ? "bg-white text-ink" : "bg-bad text-white"}`}>{waiting}</span>
                )}
              </button>
            ))}
          </nav>
          <div className="text-right leading-tight">
            <div className="text-xs text-muted">Trucks leave {when(f.deadline)} in</div>
            <div className="text-lg font-semibold tabular-nums">{countdown(hoursLeft)}</div>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-5 pb-20 pt-7">
        {page === "today" && <Today data={data} onOpen={setOpen} />}
        {page === "parts" && <Parts data={data} onOpen={setOpen} />}
        {page === "activity" && <Activity data={data} onOpen={setOpen} />}
        {page === "agents" && <Agents data={data} />}
      </main>

      {error && <div className="fixed bottom-4 left-1/2 -translate-x-1/2 rounded-lg bg-ink px-4 py-2 text-sm text-white">{error}</div>}
      {opened && (
        <CasePanel
          item={opened}
          findings={data.findings.filter((x) => x.material_id === opened.material_id)}
          onClose={() => setOpen(null)}
          onChanged={load}
        />
      )}
    </div>
  );
}
