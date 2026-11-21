import type { ReactNode } from "react";
import type { CaseState } from "./api";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-xl border border-line bg-card ${className}`}>{children}</section>;
}

export function Label({ children }: { children: ReactNode }) {
  return <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">{children}</div>;
}

type Tone = "bad" | "warn" | "good" | "neutral";
const TONES: Record<Tone, string> = {
  bad: "bg-bad-soft text-bad",
  warn: "bg-warn-soft text-warn",
  good: "bg-good-soft text-good",
  neutral: "bg-paper text-muted",
};

export function Chip({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className={`inline-block max-w-full truncate rounded-full px-2.5 py-0.5 align-bottom text-xs font-medium ${TONES[tone]}`}>
      {children}
    </span>
  );
}

const STATES: Record<CaseState, { label: string; tone: Tone }> = {
  needs_human: { label: "Handed over to you", tone: "bad" },
  needs_approval: { label: "Needs your approval", tone: "bad" },
  supplier_answered: { label: "Supplier answered", tone: "warn" },
  waiting_for_supplier: { label: "Waiting for supplier", tone: "neutral" },
  proposed: { label: "Recommendation ready", tone: "warn" },
};

export function StateChip({ state }: { state: CaseState }) {
  return <Chip tone={STATES[state].tone}>{STATES[state].label}</Chip>;
}

export function Button({
  children,
  onClick,
  kind = "primary",
  disabled,
}: {
  children: ReactNode;
  onClick?: () => void;
  kind?: "primary" | "quiet";
  disabled?: boolean;
}) {
  const style =
    kind === "primary"
      ? "bg-accent text-white hover:bg-[#182d4a] disabled:bg-[#9aa7b8]"
      : "border border-line bg-card text-ink hover:bg-paper disabled:text-muted";
  return (
    <button onClick={onClick} disabled={disabled} className={`rounded-lg px-3.5 py-2 text-sm font-medium transition-colors ${style}`}>
      {children}
    </button>
  );
}
