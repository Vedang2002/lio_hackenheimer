const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** The server speaks Europe/Berlin wall-clock time without an offset; read it as such, whatever the browser's zone. */
export function parts(iso: string) {
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/.exec(iso);
  if (!m) return null;
  const [y, mo, d, h, mi] = m.slice(1).map((v) => Number(v ?? 0));
  return { y, mo, d, h, mi, utc: Date.UTC(y, mo - 1, d, h, mi) };
}

export function when(iso: string | null | undefined): string {
  const p = iso ? parts(iso) : null;
  if (!p) return iso ?? "";
  const day = DAYS[new Date(p.utc).getUTCDay()];
  const time = `${String(p.h).padStart(2, "0")}:${String(p.mi).padStart(2, "0")}`;
  return iso!.includes("T") ? `${day} ${time}` : `${day} ${p.d}.${p.mo}.`;
}

/** Timestamps the backend stores in UTC (with an offset), shown in Berlin time. */
export function clock(isoWithOffset: string | null | undefined): string {
  if (!isoWithOffset) return "";
  const d = new Date(isoWithOffset);
  if (Number.isNaN(d.getTime())) return isoWithOffset;
  return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Berlin" });
}

export function hoursBetween(fromIso: string, toIso: string): number {
  const a = parts(fromIso);
  const b = parts(toIso);
  return a && b ? (b.utc - a.utc) / 3_600_000 : 0;
}

export function countdown(hours: number): string {
  if (hours <= 0) return "now";
  const h = Math.floor(hours);
  const m = Math.floor((hours - h) * 60);
  return h ? `${h} h ${String(m).padStart(2, "0")} min` : `${m} min`;
}

export const num = (n: number | null | undefined) => (n == null ? "–" : Math.round(n).toLocaleString("en-GB"));

export function money(amount: number | null | undefined, currency: string | null | undefined): string {
  if (amount == null) return "cost not known";
  return `${currency === "EUR" || !currency ? "€" : `${currency} `}${Math.round(amount).toLocaleString("en-GB")}`;
}

export const fileName = (path: string) => path.split("/").pop() ?? path;
