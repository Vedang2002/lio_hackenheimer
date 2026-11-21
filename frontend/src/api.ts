export type Stop = { at: string; material_id: string };

export type ProductForecast = {
  product_id: string;
  name: string | null;
  day_one_demand: number;
  packed: number;
  to_pack: number;
  packed_by_deadline: number;
  short: number;
  rate_per_hour: number;
  hours_needed: number | null;
  first_stop: Stop | null;
  finished_at: string | null;
};

export type ComponentForecast = {
  material_id: string;
  description: string | null;
  need: number;
  erp_stock: number;
  usable_stock: number;
  inbound_counted: number;
  inbound_needing_action: number;
  confirmed_by_supplier: number;
  gap: number;
  used_in: string[];
};

export type Forecast = {
  ready: boolean;
  reason?: string;
  as_of: string;
  deadline: string;
  hours_available: number;
  will_make_it: boolean;
  timeline: ({ at: string } & Record<string, number | string>)[];
  products: ProductForecast[];
  components: ComponentForecast[];
  assumptions: string[];
};

export type Evidence = { path: string; note: string };

export type Finding = {
  key: string;
  kind: string;
  level: "stops_line" | "watch" | "info";
  material_id: string | null;
  description: string | null;
  title: string;
  detail: string;
  units: number;
  needs_action: boolean;
  evidence: Evidence[];
};

export type Option = {
  title: string;
  description: string;
  copies_saved: number;
  cost: number | null;
  currency: string | null;
  arrives_by: string | null;
  risks: string | null;
};

export type Reading = {
  summary: string;
  accepts: boolean | null;
  deliveries: { material_id: string; quantity: number; dock_at: string | null; firm: boolean; condition: string | null }[];
  costs?: { what: string; amount: number; currency: string | null }[];
  needs_from_us: string[];
  questions: string[];
};

export type Message = {
  id: number;
  direction: "out" | "in";
  status: "draft" | "internal" | "blocked" | "sent" | "received";
  counterpart_name: string | null;
  counterpart_email: string | null;
  language: string | null;
  subject: string;
  body: string;
  body_english: string | null;
  purpose: string | null;
  created_at: string;
  sent_at: string | null;
  reading: Reading | null;
  commits: "nothing" | "money_or_terms" | null;
  sent_by: "agent" | "human" | "other" | "auto" | null;
  approved_by: string | null;
  held_because: string | null;
};

export type CaseState = "needs_human" | "needs_approval" | "supplier_answered" | "waiting_for_supplier" | "proposed";

export type Case = {
  key: string;
  material_id: string;
  description: string | null;
  state: CaseState;
  needs_human: string | null;
  cost_usd: number;
  card: { headline: string; problem: string; suggestion: string; your_step: string } | null;
  saves_alone: number;
  then_limited_by: string[];
  updated_at: string;
  summary: string | null;
  why: string | null;
  recommended: Option | null;
  options: Option[];
  recommended_option: number;
  ruled_out: string[];
  approval: { required_role: string; person: string | null; basis: string; note: string | null } | null;
  open_questions: string[];
  checks: string[];
  evidence: string[];
  trace: { tool: string; args: Record<string, unknown> }[];
  messages: Message[];
};

export type Overview = {
  now: string;
  forecast: Forecast;
  erp_forecast: Forecast;
  forecast_if_acted: Forecast;
  priority: { order: string[]; basis: string | null; path: string | null };
  findings: Finding[];
  cases: Case[];
  drops: { ts: string; path: string; kind: string }[];
  last_sync: { started_at: string; listed: number } | null;
  files: number;
  documents_read: number;
  needs_review: { path: string; detail: string }[];
  spend: { today_usd: number; budget_usd: number; by_kind: { kind: string; usd: number; calls: number }[] };
  activity: { at: string | null; kind: "files" | "reply" | "sent_by_agent" | "sent_by_human" | "handover"; case_key: string | null; text: string; detail: string | null }[];
  me: { name: string | null; role: "viewer" | "approver" | "admin"; limit_eur: number | null; verified: boolean };
  audit: { ts: string; actor: string; action: string; case_key: string | null; detail: string | null }[];
  plan: { short_now: number; short_if_all_solved: number | null };
  foreign_mail: { counterpart_name: string | null; subject: string; sent_at: string }[];
  agent_work: { sent_by_agents: number; approved_by_people: number; replies: number; findings: number };
  loop: { last_tick: { at: string; errors: string[] } | null; running: boolean; agents_on: boolean };
  mail_address: string | null;
};

const KEY = "foredock.key";
export const accessKey = () => localStorage.getItem(KEY) ?? "";
export const setAccessKey = (key: string) => localStorage.setItem(KEY, key);

export class Unauthorized extends Error {}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", "X-Access-Key": accessKey(), ...init?.headers },
  });
  if (r.status === 401) throw new Unauthorized();
  if (!r.ok) {
    const detail = await r.json().then((b) => b.detail).catch(() => r.statusText);
    throw new Error(detail || `Request failed (${r.status})`);
  }
  return r.json();
}

export const getOverview = () => call<Overview>("/api/overview");
export const sendMessage = (id: number, approver: string) =>
  call(`/api/messages/${id}/send`, { method: "POST", headers: { "X-Approver": encodeURIComponent(approver) } });
export const editDraft = (id: number, subject: string, body: string) =>
  call(`/api/messages/${id}`, { method: "PATCH", body: JSON.stringify({ subject, body }) });
export const reworkCase = (materialId: string) => call(`/api/cases/${materialId}/rework`, { method: "POST" });
export const fileUrl = (path: string) => `/api/file?path=${encodeURIComponent(path)}&key=${encodeURIComponent(accessKey())}`;
