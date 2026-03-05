/** Format price as $0.XX */
export function fmtPrice(prob: number): string {
  return `$${prob.toFixed(2)}`;
}

/** Format edge as +$0.XX or -$0.XX */
export function fmtEdge(edge: number): string {
  return `${edge >= 0 ? "+" : ""}$${edge.toFixed(2)}`;
}

/** Format edge in cents: +11.7c */
export function fmtEdgeCents(edge: number): string {
  const c = edge * 100;
  return `${c >= 0 ? "+" : ""}${c.toFixed(1)}c`;
}

/** Format dollars with $ sign */
export function fmtDollar(amount: number): string {
  return `$${amount.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
}

/** Format volume compactly */
export function fmtVolume(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(0)}k`;
  return String(v);
}

/** Format timestamp as relative time ("2m ago", "1h ago") */
export function fmtRelativeTime(ts: string): string {
  try {
    const now = Date.now();
    const then = new Date(ts).getTime();
    const diffSec = Math.floor((now - then) / 1000);

    if (diffSec < 5) return "now";
    if (diffSec < 60) return `${diffSec}s ago`;
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
    return `${Math.floor(diffSec / 86400)}d ago`;
  } catch {
    return ts;
  }
}
