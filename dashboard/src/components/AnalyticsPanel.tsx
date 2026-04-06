"use client";

import { api } from "@/lib/api";
import PanelHeader from "./PanelHeader";
import { useState, useEffect, useMemo } from "react";
import type { AnalyticsData } from "@/lib/types";
import { fmtDollar } from "@/lib/format";

const STRATEGY_COLORS: Record<string, string> = {
  convergence: "#3b82f6",
  momentum: "#f59e0b",
  mean_reversion: "#00d26a",
  event_driven: "#a855f7",
};

const REGIME_COLORS: Record<string, string> = {
  TRENDING: "#f59e0b",
  MEAN_REVERTING: "#00d26a",
  HIGH_VOLATILITY: "#ff3b3b",
  CONVERGENCE: "#3b82f6",
  STALE: "#555",
};

export default function AnalyticsPanel() {
  const [data, setData] = useState<AnalyticsData | null>(null);

  useEffect(() => {
    const load = () => api.getAnalytics().then(setData).catch(() => {});
    load();
    const interval = setInterval(load, 60000);
    return () => clearInterval(interval);
  }, []);

  if (!data) {
    return (
      <div className="flex flex-col h-full">
        <PanelHeader title="Analytics" />
        <div className="flex-1 flex items-center justify-center text-text-secondary text-xs">
          Loading analytics...
        </div>
      </div>
    );
  }

  const hasTrades = data.pnl_curve.length > 0;

  return (
    <div className="flex flex-col h-full">
      <PanelHeader title="Analytics" />
      <div className="flex-1 overflow-y-auto p-3 space-y-3 min-h-0">
        {!hasTrades ? (
          <div className="text-center text-text-secondary text-[10px] py-8">
            No closed trades yet. Analytics will appear after your first closed position.
          </div>
        ) : (
          <>
            <PnLCurve data={data} />
            <DrawdownSection data={data} />
            <SectorHeatmap data={data} />
            <WinLossSection data={data} />
            <AttributionSection data={data} />
          </>
        )}
      </div>
    </div>
  );
}

// ── P&L Curve ────────────────────────────────────────────────────────────

function PnLCurve({ data }: { data: AnalyticsData }) {
  const curve = data.pnl_curve;
  if (curve.length < 2) return null;

  const W = 320;
  const H = 80;
  const PAD = 4;

  const values = curve.map((p) => p.cumulative_pnl);
  const minVal = Math.min(0, ...values);
  const maxVal = Math.max(0, ...values);
  const range = maxVal - minVal || 1;

  const toX = (i: number) => PAD + ((W - 2 * PAD) * i) / (curve.length - 1);
  const toY = (v: number) => H - PAD - ((v - minVal) / range) * (H - 2 * PAD);

  const points = curve.map((p, i) => `${toX(i)},${toY(p.cumulative_pnl)}`).join(" ");
  const zeroY = toY(0);
  const lastPnl = curve[curve.length - 1].cumulative_pnl;
  const lineColor = lastPnl >= 0 ? "#00d26a" : "#ff3b3b";

  // Fill area
  const fillPoints = `${toX(0)},${zeroY} ${points} ${toX(curve.length - 1)},${zeroY}`;

  return (
    <div>
      <div className="text-[9px] text-text-secondary uppercase tracking-wider mb-1 flex justify-between">
        <span>Cumulative P&L</span>
        <span className={lastPnl >= 0 ? "text-green" : "text-red"}>{fmtDollar(lastPnl)}</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 80 }}>
        {/* Zero line */}
        <line x1={PAD} y1={zeroY} x2={W - PAD} y2={zeroY} stroke="#1e1e2e" strokeWidth="0.5" />
        {/* Fill */}
        <polygon points={fillPoints} fill={lineColor} opacity={0.1} />
        {/* Line */}
        <polyline points={points} fill="none" stroke={lineColor} strokeWidth="1.5" />
        {/* Last point */}
        <circle cx={toX(curve.length - 1)} cy={toY(lastPnl)} r="2.5" fill={lineColor} />
      </svg>
    </div>
  );
}

// ── Drawdown ─────────────────────────────────────────────────────────────

function DrawdownSection({ data }: { data: AnalyticsData }) {
  const dd = data.drawdown;

  const gaugeRadius = 30;
  const circumference = 2 * Math.PI * gaugeRadius;
  const ddPct = Math.min(dd.current_drawdown_pct * 100, 100);
  const offset = circumference - (ddPct / 100) * circumference;
  const color = ddPct > 15 ? "#ff3b3b" : ddPct > 10 ? "#f59e0b" : "#00d26a";

  return (
    <div>
      <div className="text-[9px] text-text-secondary uppercase tracking-wider mb-1">Drawdown</div>
      <div className="flex items-center gap-3">
        <div className="relative w-16 h-16 shrink-0">
          <svg viewBox="0 0 80 80" className="w-full h-full -rotate-90">
            <circle cx="40" cy="40" r={gaugeRadius} fill="none" stroke="#1e1e2e" strokeWidth="6" />
            <circle
              cx="40" cy="40" r={gaugeRadius}
              fill="none" stroke={color} strokeWidth="6"
              strokeDasharray={circumference}
              strokeDashoffset={offset}
              strokeLinecap="round"
            />
          </svg>
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-[10px] font-mono" style={{ color }}>{ddPct.toFixed(1)}%</span>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[10px]">
          <div>
            <div className="text-text-secondary">Max DD</div>
            <div className="font-mono text-red">{(dd.max_drawdown_pct * 100).toFixed(2)}%</div>
          </div>
          <div>
            <div className="text-text-secondary">Max DD $</div>
            <div className="font-mono text-red">{fmtDollar(dd.max_drawdown_dollars)}</div>
          </div>
          <div>
            <div className="text-text-secondary">Current DD</div>
            <div className="font-mono">{fmtDollar(dd.current_drawdown_dollars)}</div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Sector Heatmap ───────────────────────────────────────────────────────

function SectorHeatmap({ data }: { data: AnalyticsData }) {
  const sectors = data.sector_heatmap;
  if (sectors.length === 0) return null;

  const maxPnl = Math.max(...sectors.map((s) => Math.abs(s.pnl)), 1);

  return (
    <div>
      <div className="text-[9px] text-text-secondary uppercase tracking-wider mb-1">Sector Performance</div>
      <div className="grid grid-cols-3 gap-1">
        {sectors.map((s) => {
          const intensity = Math.min(Math.abs(s.pnl) / maxPnl, 1);
          const bg = s.pnl >= 0
            ? `rgba(0, 210, 106, ${0.1 + intensity * 0.3})`
            : `rgba(255, 59, 59, ${0.1 + intensity * 0.3})`;
          return (
            <div
              key={s.category}
              className="p-1.5 rounded text-center"
              style={{ background: bg }}
            >
              <div className="text-[9px] text-text-primary truncate">{s.category}</div>
              <div className={`text-[10px] font-mono ${s.pnl >= 0 ? "text-green" : "text-red"}`}>
                {fmtDollar(s.pnl)}
              </div>
              <div className="text-[8px] text-text-secondary">
                {s.trades}t | {(s.win_rate * 100).toFixed(0)}%
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Win/Loss Analysis ────────────────────────────────────────────────────

function WinLossSection({ data }: { data: AnalyticsData }) {
  const wl = data.win_loss;
  const total = wl.total_wins + wl.total_losses;
  if (total === 0) return null;

  const winPct = (wl.total_wins / total) * 100;
  const lossPct = 100 - winPct;

  return (
    <div>
      <div className="text-[9px] text-text-secondary uppercase tracking-wider mb-1">Win / Loss</div>
      {/* Win/Loss bar */}
      <div className="flex h-3 rounded overflow-hidden mb-2">
        <div className="bg-green" style={{ width: `${winPct}%` }} />
        <div className="bg-red" style={{ width: `${lossPct}%` }} />
      </div>
      <div className="flex justify-between text-[9px] mb-2">
        <span className="text-green">{wl.total_wins}W ({winPct.toFixed(0)}%)</span>
        <span className="text-red">{wl.total_losses}L ({lossPct.toFixed(0)}%)</span>
      </div>
      {/* Stats grid */}
      <div className="grid grid-cols-3 gap-2">
        <StatCard label="Avg Win" value={fmtDollar(wl.avg_win)} color="text-green" />
        <StatCard label="Avg Loss" value={fmtDollar(wl.avg_loss)} color="text-red" />
        <StatCard
          label="Win/Loss"
          value={wl.avg_loss !== 0 ? (Math.abs(wl.avg_win / wl.avg_loss)).toFixed(2) : "-"}
          color="text-text-primary"
        />
        <StatCard label="Best" value={fmtDollar(wl.largest_win)} color="text-green" />
        <StatCard label="Worst" value={fmtDollar(wl.largest_loss)} color="text-red" />
        <StatCard
          label="Streak"
          value={`${wl.current_streak}${wl.current_streak_type === "win" ? "W" : "L"}`}
          color={wl.current_streak_type === "win" ? "text-green" : "text-red"}
        />
      </div>
    </div>
  );
}

function StatCard({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="bg-bg rounded p-1.5 text-center">
      <div className="text-[8px] text-text-secondary">{label}</div>
      <div className={`text-[10px] font-mono ${color}`}>{value}</div>
    </div>
  );
}

// ── Performance Attribution ──────────────────────────────────────────────

function AttributionSection({ data }: { data: AnalyticsData }) {
  const [tab, setTab] = useState<"strategy" | "regime" | "category">("strategy");
  const attr = data.attribution;

  const entries = useMemo(() => {
    const source = tab === "strategy" ? attr.by_strategy
      : tab === "regime" ? attr.by_regime
      : attr.by_category;
    return Object.entries(source || {}).sort((a, b) => b[1].pnl - a[1].pnl);
  }, [tab, attr]);

  const colorMap = tab === "strategy" ? STRATEGY_COLORS : tab === "regime" ? REGIME_COLORS : {};

  return (
    <div>
      <div className="text-[9px] text-text-secondary uppercase tracking-wider mb-1">Attribution</div>
      {/* Tab bar */}
      <div className="flex gap-1 mb-2">
        {(["strategy", "regime", "category"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-2 py-0.5 text-[9px] rounded ${
              tab === t
                ? "bg-blue/20 text-blue"
                : "text-text-secondary hover:text-text-primary"
            }`}
          >
            {t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>
      {/* Table */}
      <table className="w-full text-[10px]">
        <thead>
          <tr className="text-text-secondary border-b border-border">
            <th className="text-left py-0.5 font-normal">Name</th>
            <th className="text-right py-0.5 font-normal">Trades</th>
            <th className="text-right py-0.5 font-normal">WR</th>
            <th className="text-right py-0.5 font-normal">P&L</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([name, stats]) => (
            <tr key={name} className="border-b border-border/30 hover:bg-surface">
              <td className="py-0.5">
                <span className="flex items-center gap-1">
                  {colorMap[name] && (
                    <span className="w-2 h-2 rounded-full inline-block" style={{ background: colorMap[name] }} />
                  )}
                  <span className="font-mono">{name}</span>
                </span>
              </td>
              <td className="text-right font-mono">{stats.trades}</td>
              <td className="text-right font-mono">
                {stats.win_rate !== undefined ? `${(stats.win_rate * 100).toFixed(0)}%` : "-"}
              </td>
              <td className={`text-right font-mono ${stats.pnl >= 0 ? "text-green" : "text-red"}`}>
                {fmtDollar(stats.pnl)}
              </td>
            </tr>
          ))}
          {entries.length === 0 && (
            <tr>
              <td colSpan={4} className="text-center text-text-secondary py-2">No data</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
