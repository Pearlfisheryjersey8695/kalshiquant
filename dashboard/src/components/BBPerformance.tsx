"use client";

import { api } from "@/lib/api";
import { useState, useEffect, useMemo } from "react";
import type { AnalyticsData, JournalSummary } from "@/lib/types";

// ── Helpers ─────────────────────────────────────────────────────────────────

function pnlColor(v: number) {
  return v > 0 ? "text-green-400" : v < 0 ? "text-red-400" : "text-[#888899]";
}

function pnlSign(v: number) {
  return v >= 0 ? `+$${v.toFixed(2)}` : `-$${Math.abs(v).toFixed(2)}`;
}

// ── Component ───────────────────────────────────────────────────────────────

export default function BBPerformance() {
  const [analytics, setAnalytics] = useState<AnalyticsData | null>(null);
  const [summary, setSummary] = useState<JournalSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const fetchData = () => {
    Promise.all([
      api.getAnalytics(),
      api.getJournalSummary().catch(() => null),
    ])
      .then(([a, s]) => { setAnalytics(a); setSummary(s); setError(""); })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { fetchData(); }, []);

  // Compute metrics from analytics
  const metrics = useMemo(() => {
    if (!analytics) return null;
    const dd = analytics.drawdown;
    const wl = analytics.win_loss;

    // Compute Sharpe/Sortino/Calmar from PnL curve
    const pnlPts = analytics.pnl_curve;
    const returns: number[] = [];
    for (let i = 1; i < pnlPts.length; i++) {
      returns.push(pnlPts[i].cumulative_pnl - pnlPts[i - 1].cumulative_pnl);
    }
    const mean = returns.length > 0 ? returns.reduce((a, b) => a + b, 0) / returns.length : 0;
    const std = returns.length > 1
      ? Math.sqrt(returns.reduce((a, b) => a + (b - mean) ** 2, 0) / (returns.length - 1))
      : 1;
    const downside = returns.length > 1
      ? Math.sqrt(returns.filter(r => r < 0).reduce((a, b) => a + b ** 2, 0) / returns.length)
      : 1;

    const sharpe = std > 0 ? (mean / std) * Math.sqrt(252) : 0;
    const sortino = downside > 0 ? (mean / downside) * Math.sqrt(252) : 0;
    const mddPct = dd.max_drawdown_pct;
    const calmar = mddPct > 0 ? (mean * 252) / (mddPct * 100) : 0;

    return { sharpe, sortino, calmar, mddPct, returns, wl, dd };
  }, [analytics]);

  // Compute rolling Sharpe (20-point window)
  const rollingSharpe = useMemo(() => {
    if (!metrics || metrics.returns.length < 20) return [];
    const window = 20;
    const result: { idx: number; sharpe: number }[] = [];
    for (let i = window; i <= metrics.returns.length; i++) {
      const slice = metrics.returns.slice(i - window, i);
      const m = slice.reduce((a, b) => a + b, 0) / window;
      const s = Math.sqrt(slice.reduce((a, b) => a + (b - m) ** 2, 0) / (window - 1));
      result.push({ idx: i, sharpe: s > 0 ? (m / s) * Math.sqrt(252) : 0 });
    }
    return result;
  }, [metrics]);

  // Win/loss streak timeline
  const streakTimeline = useMemo(() => {
    if (!analytics) return [];
    const pnlPts = analytics.pnl_curve;
    const timeline: boolean[] = [];
    for (let i = 1; i < pnlPts.length; i++) {
      timeline.push(pnlPts[i].cumulative_pnl > pnlPts[i - 1].cumulative_pnl);
    }
    return timeline.slice(-60); // last 60
  }, [analytics]);

  if (loading) {
    return (
      <div className="h-full bg-[#0a0a0f] flex items-center justify-center">
        <div className="text-[11px] text-[#888899] animate-pulse font-mono">LOADING PERFORMANCE...</div>
      </div>
    );
  }

  if (error || !analytics || !metrics) {
    return (
      <div className="h-full bg-[#0a0a0f] flex flex-col items-center justify-center gap-3">
        <div className="text-[11px] text-red-400 font-mono">PERFORMANCE UNAVAILABLE</div>
        <div className="text-[9px] text-[#888899]">{error}</div>
        <button onClick={fetchData} className="text-[10px] text-bb-orange border border-bb-orange/30 px-3 py-1 hover:bg-bb-orange/10">
          RETRY
        </button>
      </div>
    );
  }

  // Strategy attribution data
  const stratAttrib = summary?.by_strategy
    ? Object.entries(summary.by_strategy).sort((a, b) => b[1].pnl - a[1].pnl)
    : Object.entries(analytics.attribution.by_strategy).map(([k, v]) => [k, { ...v, wins: 0, win_rate: v.win_rate ?? 0 }] as const).sort((a, b) => b[1].pnl - a[1].pnl);

  const maxStratPnl = stratAttrib.length > 0 ? Math.max(...stratAttrib.map(([, v]) => Math.abs(v.pnl)), 1) : 1;

  // Drawdown curve
  const ddCurve = analytics.drawdown.drawdown_curve;

  return (
    <div className="h-full bg-[#0a0a0f] overflow-y-auto font-mono">
      {/* Header */}
      <div className="h-[32px] bg-[#12121a] border-b border-[#1e1e2e] flex items-center px-4 shrink-0">
        <span className="text-bb-orange text-[11px] font-bold tracking-wider">PERFORMANCE</span>
      </div>

      {/* Top metrics strip */}
      <div className="grid grid-cols-4 border-b border-[#1e1e2e]">
        <MetricCell label="SHARPE" value={metrics.sharpe.toFixed(2)} good={metrics.sharpe > 1} />
        <MetricCell label="SORTINO" value={metrics.sortino.toFixed(2)} good={metrics.sortino > 1} />
        <MetricCell label="CALMAR" value={metrics.calmar.toFixed(2)} good={metrics.calmar > 1} />
        <MetricCell label="MAX DD" value={`-${(metrics.mddPct * 100).toFixed(1)}%`} good={metrics.mddPct < 0.05} />
      </div>

      {/* Main grid */}
      <div className="grid grid-cols-2 gap-0">
        {/* Rolling Sharpe chart */}
        <div className="border-r border-b border-[#1e1e2e]">
          <SectionHeader title="ROLLING SHARPE (20-PERIOD)" />
          <div className="px-4 py-3 h-[180px]">
            {rollingSharpe.length > 0 ? (
              <RollingSharpeChart data={rollingSharpe} />
            ) : (
              <div className="h-full flex items-center justify-center text-[10px] text-[#888899]">
                Insufficient data for rolling Sharpe
              </div>
            )}
          </div>
        </div>

        {/* Strategy Attribution */}
        <div className="border-b border-[#1e1e2e]">
          <SectionHeader title="STRATEGY ATTRIBUTION" />
          <div className="px-4 py-3 max-h-[180px] overflow-y-auto">
            {stratAttrib.length === 0 ? (
              <div className="text-[10px] text-[#888899] text-center py-4">No strategy data</div>
            ) : (
              stratAttrib.map(([name, data]) => (
                <div key={name} className="flex items-center gap-2 mb-2">
                  <span className="text-[10px] text-bb-white w-24 truncate">{name}</span>
                  <div className="flex-1 h-3 bg-[#1e1e2e] relative">
                    <div
                      className={`h-full ${data.pnl >= 0 ? "bg-green-500/50" : "bg-red-500/50"}`}
                      style={{ width: `${Math.min((Math.abs(data.pnl) / maxStratPnl) * 100, 100)}%` }}
                    />
                  </div>
                  <span className={`text-[10px] w-16 text-right ${pnlColor(data.pnl)}`}>{pnlSign(data.pnl)}</span>
                  <span className="text-[9px] text-[#888899] w-14 text-right">
                    {((data.win_rate ?? 0) * 100).toFixed(0)}% WR
                  </span>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Drawdown Chart */}
        <div className="border-r border-[#1e1e2e]">
          <SectionHeader title="DRAWDOWN" />
          <div className="px-4 py-3 h-[180px]">
            {ddCurve.length > 0 ? (
              <DrawdownChart data={ddCurve} />
            ) : (
              <div className="h-full flex items-center justify-center text-[10px] text-[#888899]">
                No drawdown data
              </div>
            )}
          </div>
        </div>

        {/* Win/Loss Streaks */}
        <div>
          <SectionHeader title="WIN / LOSS TIMELINE" />
          <div className="px-4 py-3">
            {/* Stats */}
            {metrics.wl && (
              <div className="grid grid-cols-4 gap-2 mb-3 text-[10px]">
                <span className="text-[#888899]">Wins: <span className="text-green-400">{metrics.wl.total_wins}</span></span>
                <span className="text-[#888899]">Losses: <span className="text-red-400">{metrics.wl.total_losses}</span></span>
                <span className="text-[#888899]">Best Streak: <span className="text-green-400">{metrics.wl.max_consecutive_wins}</span></span>
                <span className="text-[#888899]">Worst Streak: <span className="text-red-400">{metrics.wl.max_consecutive_losses}</span></span>
                <span className="text-[#888899]">Avg Win: <span className="text-green-400">{pnlSign(metrics.wl.avg_win)}</span></span>
                <span className="text-[#888899]">Avg Loss: <span className="text-red-400">{pnlSign(metrics.wl.avg_loss)}</span></span>
                <span className="text-[#888899]">Best: <span className="text-green-400">{pnlSign(metrics.wl.largest_win)}</span></span>
                <span className="text-[#888899]">Worst: <span className="text-red-400">{pnlSign(metrics.wl.largest_loss)}</span></span>
              </div>
            )}
            {/* Timeline blocks */}
            <div className="flex flex-wrap gap-0.5 mt-2">
              {streakTimeline.map((win, i) => (
                <div
                  key={i}
                  className={`w-3 h-3 ${win ? "bg-green-500/60" : "bg-red-500/60"}`}
                  title={win ? "Win" : "Loss"}
                />
              ))}
              {streakTimeline.length === 0 && (
                <div className="text-[10px] text-[#888899]">No timeline data</div>
              )}
            </div>
            {/* Current streak */}
            {metrics.wl && metrics.wl.current_streak > 0 && (
              <div className="mt-2 text-[10px]">
                <span className="text-[#888899]">Current: </span>
                <span className={metrics.wl.current_streak_type === "win" ? "text-green-400" : "text-red-400"}>
                  {metrics.wl.current_streak} {metrics.wl.current_streak_type === "win" ? "WINS" : "LOSSES"}
                </span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Sector Heatmap */}
      {analytics.sector_heatmap.length > 0 && (
        <div className="border-t border-[#1e1e2e]">
          <SectionHeader title="SECTOR HEATMAP" />
          <div className="grid grid-cols-4 gap-1 p-4">
            {analytics.sector_heatmap.map(s => (
              <div
                key={s.category}
                className={`p-2 border border-[#1e1e2e] ${s.pnl >= 0 ? "bg-green-500/10" : "bg-red-500/10"}`}
              >
                <div className="text-[9px] text-[#888899] truncate">{s.category}</div>
                <div className={`text-[12px] font-bold ${pnlColor(s.pnl)}`}>{pnlSign(s.pnl)}</div>
                <div className="text-[8px] text-[#888899]">{s.trades} trades | {(s.win_rate * 100).toFixed(0)}% WR</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Sub-components ──────────────────────────────────────────────────────────

function MetricCell({ label, value, good }: { label: string; value: string; good: boolean }) {
  return (
    <div className="bg-[#12121a] border-r border-[#1e1e2e] last:border-r-0 px-4 py-3 text-center">
      <div className="text-[8px] text-[#888899] tracking-wider mb-1">{label}</div>
      <div className={`text-[16px] font-bold ${good ? "text-green-400" : "text-amber-400"}`}>{value}</div>
    </div>
  );
}

function SectionHeader({ title }: { title: string }) {
  return (
    <div className="h-[24px] bg-[#12121a] border-b border-[#1e1e2e] flex items-center px-3">
      <span className="text-bb-orange text-[9px] tracking-wider font-bold">{title}</span>
    </div>
  );
}

// ── SVG Charts ──────────────────────────────────────────────────────────────

function RollingSharpeChart({ data }: { data: { idx: number; sharpe: number }[] }) {
  const W = 400;
  const H = 140;
  const pad = { t: 10, r: 10, b: 20, l: 40 };
  const cw = W - pad.l - pad.r;
  const ch = H - pad.t - pad.b;

  const minS = Math.min(...data.map(d => d.sharpe), 0);
  const maxS = Math.max(...data.map(d => d.sharpe), 1);
  const range = maxS - minS || 1;

  const x = (i: number) => pad.l + (i / (data.length - 1 || 1)) * cw;
  const y = (v: number) => pad.t + ch - ((v - minS) / range) * ch;

  const pathD = data.map((d, i) => `${i === 0 ? "M" : "L"} ${x(i).toFixed(1)} ${y(d.sharpe).toFixed(1)}`).join(" ");
  const zeroY = y(0);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-full">
      {/* Zero line */}
      <line x1={pad.l} x2={W - pad.r} y1={zeroY} y2={zeroY} stroke="#1e1e2e" strokeWidth="1" strokeDasharray="4 2" />
      {/* Y-axis labels */}
      <text x={pad.l - 4} y={pad.t + 4} textAnchor="end" fill="#888899" fontSize="8">{maxS.toFixed(1)}</text>
      <text x={pad.l - 4} y={H - pad.b} textAnchor="end" fill="#888899" fontSize="8">{minS.toFixed(1)}</text>
      <text x={pad.l - 4} y={zeroY + 3} textAnchor="end" fill="#888899" fontSize="8">0</text>
      {/* Line */}
      <path d={pathD} fill="none" stroke="#f97316" strokeWidth="1.5" />
      {/* Current value label */}
      {data.length > 0 && (
        <text x={x(data.length - 1)} y={y(data[data.length - 1].sharpe) - 6} textAnchor="end" fill="#f97316" fontSize="9" fontWeight="bold">
          {data[data.length - 1].sharpe.toFixed(2)}
        </text>
      )}
    </svg>
  );
}

function DrawdownChart({ data }: { data: { ts: string; drawdown_pct: number }[] }) {
  const W = 400;
  const H = 140;
  const pad = { t: 10, r: 10, b: 20, l: 40 };
  const cw = W - pad.l - pad.r;
  const ch = H - pad.t - pad.b;

  const maxDD = Math.max(...data.map(d => Math.abs(d.drawdown_pct)), 0.01);

  const x = (i: number) => pad.l + (i / (data.length - 1 || 1)) * cw;
  const y = (v: number) => pad.t + (Math.abs(v) / maxDD) * ch;

  // Area path
  const areaD = data.map((d, i) => `${i === 0 ? "M" : "L"} ${x(i).toFixed(1)} ${y(d.drawdown_pct).toFixed(1)}`).join(" ")
    + ` L ${x(data.length - 1).toFixed(1)} ${pad.t} L ${pad.l} ${pad.t} Z`;

  const lineD = data.map((d, i) => `${i === 0 ? "M" : "L"} ${x(i).toFixed(1)} ${y(d.drawdown_pct).toFixed(1)}`).join(" ");

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-full">
      {/* Zero line at top */}
      <line x1={pad.l} x2={W - pad.r} y1={pad.t} y2={pad.t} stroke="#1e1e2e" strokeWidth="1" />
      {/* Y-axis */}
      <text x={pad.l - 4} y={pad.t + 4} textAnchor="end" fill="#888899" fontSize="8">0%</text>
      <text x={pad.l - 4} y={H - pad.b} textAnchor="end" fill="#888899" fontSize="8">-{(maxDD * 100).toFixed(1)}%</text>
      {/* Area fill */}
      <path d={areaD} fill="rgba(239,68,68,0.15)" />
      {/* Line */}
      <path d={lineD} fill="none" stroke="#ef4444" strokeWidth="1.5" />
      {/* Current value */}
      {data.length > 0 && (
        <text x={x(data.length - 1)} y={y(data[data.length - 1].drawdown_pct) + 12} textAnchor="end" fill="#ef4444" fontSize="9" fontWeight="bold">
          -{(Math.abs(data[data.length - 1].drawdown_pct) * 100).toFixed(1)}%
        </text>
      )}
    </svg>
  );
}
