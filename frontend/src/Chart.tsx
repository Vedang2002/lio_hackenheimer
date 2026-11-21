import type { Forecast } from "./api";
import { num, parts, when } from "./format";

const W = 460;
const H = 210;
const PAD = { left: 6, right: 6, top: 20, bottom: 28 };

/** Copies packed over time for one edition: what the system expects against what the paperwork supports. */
export function PackChart({ productId, evidence, erp }: { productId: string; evidence: Forecast; erp: Forecast }) {
  const target = evidence.products.find((p) => p.product_id === productId)?.to_pack ?? 0;
  const t0 = parts(evidence.as_of)?.utc ?? 0;
  const t1 = parts(evidence.deadline)?.utc ?? 1;
  if (!target || t1 <= t0) return null;

  const x = (iso: string) => PAD.left + ((parts(iso)!.utc - t0) / (t1 - t0)) * (W - PAD.left - PAD.right);
  const y = (copies: number) => H - PAD.bottom - (Math.min(copies, target) / target) * (H - PAD.top - PAD.bottom);
  const path = (f: Forecast) => f.timeline.map((p, i) => `${i ? "L" : "M"}${x(p.at).toFixed(1)},${y(Number(p[productId] ?? 0)).toFixed(1)}`).join(" ");

  // Stretches where nothing is packed for hours are the night between shifts.
  const nights = evidence.timeline
    .slice(1)
    .map((p, i) => ({ from: evidence.timeline[i].at, to: p.at }))
    .filter((g) => parts(g.to)!.utc - parts(g.from)!.utc > 2 * 3_600_000);
  const end = evidence.timeline[evidence.timeline.length - 1];
  const reached = Number(end?.[productId] ?? 0);
  const short = target - reached;

  return (
    <figure>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Copies packed over time">
        {nights.map((n) => (
          <g key={n.from}>
            <rect x={x(n.from)} y={PAD.top} width={x(n.to) - x(n.from)} height={H - PAD.top - PAD.bottom} fill="#efece5" />
            <text x={(x(n.from) + x(n.to)) / 2} y={H - PAD.bottom - 8} textAnchor="middle" fontSize="12" fill="#6b7280">
              night
            </text>
          </g>
        ))}
        <line x1={PAD.left} x2={W - PAD.right} y1={y(target)} y2={y(target)} stroke="#16181d" strokeDasharray="2 4" strokeWidth="1" />
        <text x={PAD.left + 2} y={y(target) - 6} fontSize="12" fill="#16181d">
          all {num(target)} packed
        </text>
        <line x1={PAD.left} x2={W - PAD.right} y1={H - PAD.bottom} y2={H - PAD.bottom} stroke="#e6e3dc" />
        <path d={path(erp)} fill="none" stroke="#9aa7b8" strokeWidth="2" strokeDasharray="6 5" />
        <path d={path(evidence)} fill="none" stroke={short > 0 ? "#b42318" : "#1a7f4b"} strokeWidth="3" strokeLinejoin="round" />
        {short > 0 && (
          <>
            <line x1={W - PAD.right - 3} x2={W - PAD.right - 3} y1={y(target)} y2={y(reached)} stroke="#b42318" strokeWidth="2" />
            <text x={W - PAD.right - 10} y={(y(target) + y(reached)) / 2 + 5} textAnchor="end" fontSize="14" fontWeight="600" fill="#b42318">
              {num(short)} short
            </text>
          </>
        )}
        <text x={PAD.left} y={H - 9} fontSize="12" fill="#6b7280">
          {when(evidence.as_of)}
        </text>
        <text x={W - PAD.right} y={H - 9} textAnchor="end" fontSize="12" fill="#6b7280">
          trucks leave {when(evidence.deadline)}
        </text>
      </svg>
      <figcaption className="mt-1 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted">
        <span className="flex items-center gap-1.5">
          <span className={`inline-block h-0.5 w-5 ${short > 0 ? "bg-bad" : "bg-good"}`} /> what the paperwork supports
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-5 border-t-2 border-dashed border-[#9aa7b8]" /> what the system expects
        </span>
      </figcaption>
    </figure>
  );
}
