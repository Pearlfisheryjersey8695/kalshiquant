"use client";

import { useDashboard } from "@/lib/store";
import { useState, useEffect } from "react";

export default function Header() {
  const { signalsMeta, wsConnected } = useDashboard();
  const [showBacktest, setShowBacktest] = useState(false);
  const [btSummary, setBtSummary] = useState<{ trades: number; wr: number; pnl: number; prob: number } | null>(null);

  // Fetch lightweight backtest summary for header display
  useEffect(() => {
    const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    fetch(`${API_BASE}/api/backtest`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => {
        if (d && d.total_trades > 0) {
          setBtSummary({
            trades: d.total_trades,
            wr: d.win_rate,
            pnl: d.final_pnl,
            prob: d.monte_carlo?.prob_positive ?? 0,
          });
        }
      })
      .catch(() => {});
  }, []);

  // Always show simulated portfolio value for hackathon demo
  const balance = signalsMeta.portfolio_value || 10000;

  return (
    <>
      <header className="h-11 flex items-center justify-between px-4 bg-surface border-b border-border shrink-0">
        {/* Left: Brand */}
        <div className="flex items-center gap-3">
          <span className="font-mono text-base font-bold tracking-tight text-blue">KalshiQuant</span>
          <span className="text-xs text-text-secondary">v0.3</span>
        </div>

        {/* Center: Stats + Backtest button */}
        <div className="flex items-center gap-6 text-xs">
          <div>
            <span className="text-text-secondary mr-1.5">Portfolio</span>
            <span className="font-mono font-semibold text-green">
              ${balance.toLocaleString(undefined, { minimumFractionDigits: 2 })}
            </span>
          </div>
          <div>
            <span className="text-text-secondary mr-1.5">Signals</span>
            <span className="font-mono font-semibold">{signalsMeta.total_signals}</span>
          </div>
          {signalsMeta.generated_at && (
            <div>
              <span className="text-text-secondary mr-1.5">Updated</span>
              <span className="font-mono text-text-secondary">
                {new Date(signalsMeta.generated_at).toLocaleTimeString()}
              </span>
            </div>
          )}
          {btSummary && (
            <div className="flex items-center gap-1.5 text-[10px] font-mono border-l border-border pl-4 ml-2">
              <span className="text-text-secondary">{btSummary.trades}T</span>
              <span className={btSummary.wr > 0.5 ? "text-green" : "text-red"}>{(btSummary.wr * 100).toFixed(0)}%WR</span>
              <span className={btSummary.pnl >= 0 ? "text-green" : "text-red"}>
                {btSummary.pnl >= 0 ? "+" : ""}${btSummary.pnl.toFixed(0)}
              </span>
              {btSummary.prob > 0 && (
                <span className={`text-[9px] ${btSummary.prob >= 0.6 ? "text-green" : btSummary.prob >= 0.4 ? "text-amber" : "text-red"}`}>
                  {(btSummary.prob * 100).toFixed(0)}%MC
                </span>
              )}
            </div>
          )}
          <button
            onClick={() => setShowBacktest((v) => !v)}
            className="px-2 py-0.5 rounded border border-border text-[10px] font-mono text-amber hover:bg-amber/10 transition-colors"
          >
            Backtest
          </button>
        </div>

        {/* Right: Live indicator */}
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${wsConnected ? "bg-green pulse-live" : "bg-red"}`} />
          <span className={`text-xs font-mono font-semibold ${wsConnected ? "text-green" : "text-red"}`}>
            {wsConnected ? "LIVE" : "OFFLINE"}
          </span>
        </div>
      </header>

      {/* Backtest overlay — rendered via portal-like pattern */}
      {showBacktest && (
        <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-8">
          <div className="bg-surface border border-border rounded-lg max-w-4xl w-full max-h-[85vh] overflow-y-auto">
            <div className="flex items-center justify-between px-4 py-3 border-b border-border">
              <span className="font-mono text-sm font-bold text-amber">Backtest Results</span>
              <button onClick={() => setShowBacktest(false)} className="text-text-secondary hover:text-text-primary text-lg">
                &times;
              </button>
            </div>
            <div id="backtest-content" className="p-4">
              <BacktestPanel />
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// Lazy-loaded backtest panel
function BacktestPanel() {
  // Dynamic import handled by the parent — this component fetches backtest data
  const [data, setData] = useState<BacktestResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    fetch(`${API_BASE}/api/backtest`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d) => {
        setData(d);
        setLoading(false);
      })
      .catch((e) => {
        setError(e.message);
        setLoading(false);
      });
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 text-text-secondary">
        Running backtest on historical data...
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="flex items-center justify-center py-16 text-red text-sm">
        Backtest failed: {error || "No data"}
      </div>
    );
  }

  const grossPnl = data.gross_pnl ?? data.final_pnl;
  const totalFees = data.total_fees ?? 0;
  const netPnl = data.final_pnl;

  const sortino = data.sortino_ratio ?? 0;
  const holdHrs = data.avg_hold_hours ?? 0;

  const metrics = [
    { label: "Total Trades", value: String(data.total_trades), color: "", hint: "Number of round-trip trades executed" },
    { label: "Win Rate", value: `${(data.win_rate * 100).toFixed(1)}%`, color: data.win_rate > 0.5 ? "text-green" : "text-red", hint: "% of trades with positive net P&L" },
    { label: "Sharpe (per-trade)", value: data.sharpe_ratio.toFixed(2), color: data.sharpe_ratio > 0.5 ? "text-green" : data.sharpe_ratio > 0 ? "text-amber" : "text-red", hint: "Mean P&L / std dev — risk-adjusted return" },
    { label: "Sortino", value: sortino.toFixed(2), color: sortino > 0.5 ? "text-green" : sortino > 0 ? "text-amber" : "text-red", hint: "Like Sharpe but only penalizes downside" },
    { label: "Max Drawdown", value: `${(data.max_drawdown * 100).toFixed(1)}%`, color: "text-red", hint: "Largest peak-to-trough equity drop" },
    { label: "Profit Factor", value: data.profit_factor.toFixed(2), color: data.profit_factor > 1 ? "text-green" : "text-red", hint: "Gross wins / gross losses — above 1 = profitable" },
    { label: "Avg Win/Loss", value: data.avg_win_loss_ratio.toFixed(2), color: data.avg_win_loss_ratio > 1 ? "text-green" : "text-red", hint: "Average winning trade / average losing trade" },
    { label: "Avg Hold", value: holdHrs > 0 ? `${holdHrs.toFixed(1)}h` : "N/A", color: "", hint: "Average time in position" },
    { label: "Net P&L", value: `$${netPnl.toFixed(2)}`, color: netPnl > 0 ? "text-green" : "text-red", hint: "Total profit after all fees" },
    { label: "Return", value: `${(data.total_return * 100).toFixed(1)}%`, color: data.total_return > 0 ? "text-green" : "text-red", hint: "Net P&L as % of starting capital" },
  ];

  return (
    <div className="space-y-4">
      {/* Metrics cards */}
      <div className="grid grid-cols-5 gap-3">
        {metrics.map((m) => (
          <div key={m.label} className="bg-bg rounded p-3 border border-border" title={m.hint}>
            <div className="text-[10px] text-text-secondary uppercase tracking-wider mb-1">{m.label}</div>
            <div className={`font-mono text-lg font-bold ${m.color}`}>{m.value}</div>
            <div className="text-[8px] text-text-secondary/60 mt-0.5 leading-tight">{m.hint}</div>
          </div>
        ))}
      </div>

      {/* Gross vs Net P&L breakdown */}
      {totalFees > 0 && (
        <div className="flex items-center gap-4 text-[10px] px-1">
          <span className="text-text-secondary">
            Gross: <span className={`font-mono font-semibold ${grossPnl >= 0 ? "text-green" : "text-red"}`}>${grossPnl.toFixed(2)}</span>
          </span>
          <span className="text-text-secondary">
            Fees: <span className="font-mono font-semibold text-red">-${totalFees.toFixed(2)}</span>
            <span className="ml-1 opacity-60">(3c/contract RT)</span>
          </span>
          <span className="text-text-secondary">
            {data.test_period_days != null && <>Period: <span className="font-mono">{data.test_period_days.toFixed(1)}d</span></>}
          </span>
          <span className="text-text-secondary">
            {data.unique_underlyings != null && <>Underlyings: <span className="font-mono">{data.unique_underlyings}</span></>}
          </span>
        </div>
      )}

      {/* Monte Carlo headline */}
      {data.monte_carlo && data.monte_carlo.prob_positive > 0 && (
        <div className={`flex items-center gap-4 px-4 py-2.5 rounded border ${
          data.monte_carlo.prob_positive >= 0.6
            ? "bg-green/5 border-green/30"
            : data.monte_carlo.prob_positive >= 0.4
            ? "bg-amber/5 border-amber/30"
            : "bg-red/5 border-red/30"
        }`}>
          <div className="text-center">
            <div className={`font-mono text-2xl font-bold ${
              data.monte_carlo.prob_positive >= 0.6 ? "text-green" : data.monte_carlo.prob_positive >= 0.4 ? "text-amber" : "text-red"
            }`}>
              {(data.monte_carlo.prob_positive * 100).toFixed(1)}%
            </div>
            <div className="text-[9px] text-text-secondary">P(profit)</div>
          </div>
          <div className="flex-1 text-[10px] text-text-secondary">
            <span className="font-semibold text-text-primary">Bootstrap confidence</span>: Strategy is profitable in{" "}
            <span className="font-mono font-semibold">{(data.monte_carlo.prob_positive * 100).toFixed(1)}%</span> of{" "}
            <span className="font-mono">{(data.monte_carlo.n_resamples || 10000).toLocaleString()}</span> simulated scenarios.
            {data.monte_carlo.final_percentiles && (
              <span className="ml-1">
                Median: <span className="font-mono">${data.monte_carlo.final_percentiles["50"]?.toFixed(0)}</span>{" | "}
                5th: <span className="font-mono text-red">${data.monte_carlo.final_percentiles["5"]?.toFixed(0)}</span>{" | "}
                95th: <span className="font-mono text-green">${data.monte_carlo.final_percentiles["95"]?.toFixed(0)}</span>
              </span>
            )}
          </div>
        </div>
      )}

      {/* Confidence note */}
      {data.confidence_note && (
        <div className="text-[9px] text-amber/80 bg-amber/5 border border-amber/20 rounded px-3 py-1.5">
          {data.confidence_note}
        </div>
      )}

      {/* P&L curve with Monte Carlo confidence bands */}
      {data.equity_curve.length > 0 && (
        <div className="bg-bg rounded border border-border p-4">
          <div className="flex items-center justify-between mb-2">
            <div className="text-[10px] text-text-secondary uppercase tracking-wider">Cumulative P&L (net of fees)</div>
            {data.monte_carlo?.bands && Object.keys(data.monte_carlo.bands).length > 0 && (
              <div className="flex items-center gap-3 text-[8px] text-text-secondary">
                <span className="flex items-center gap-1"><span className="inline-block w-3 h-1.5 rounded-sm bg-blue/10"></span>5-95%</span>
                <span className="flex items-center gap-1"><span className="inline-block w-3 h-1.5 rounded-sm bg-blue/25"></span>25-75%</span>
                <span className="flex items-center gap-1"><span className="inline-block w-3 h-0.5 bg-blue/50"></span>Median</span>
              </div>
            )}
          </div>
          <PnLChart curve={data.equity_curve} monteCarlo={data.monte_carlo} />
        </div>
      )}

      {/* Regime Performance Heatmap */}
      {data.regime_performance && Object.keys(data.regime_performance).length > 0 && (
        <div className="bg-bg rounded border border-border">
          <div className="text-[10px] text-text-secondary uppercase tracking-wider px-3 py-2 border-b border-border">
            Regime Performance
          </div>
          <table className="w-full text-[10px]">
            <thead>
              <tr className="text-text-secondary border-b border-border">
                <th className="text-left px-3 py-1">Regime</th>
                <th className="text-center px-2 py-1">Trades</th>
                <th className="text-center px-2 py-1">Win Rate</th>
                <th className="text-right px-2 py-1">Net P&L</th>
                <th className="text-right px-2 py-1">Avg Edge</th>
                <th className="text-right px-2 py-1">Avg Fee Drag</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(data.regime_performance).map(([regime, stats]) => {
                const wrColor = stats.win_rate > 0.6 ? "text-green" : stats.win_rate > 0.4 ? "text-amber" : "text-red";
                return (
                  <tr key={regime} className="border-b border-border/30">
                    <td className="px-3 py-1.5 font-mono font-semibold">{regime}</td>
                    <td className="text-center px-2 py-1.5 font-mono">{stats.trades}</td>
                    <td className={`text-center px-2 py-1.5 font-mono font-bold ${wrColor}`}>
                      {(stats.win_rate * 100).toFixed(1)}%
                    </td>
                    <td className={`text-right px-2 py-1.5 font-mono font-semibold ${stats.net_pnl >= 0 ? "text-green" : "text-red"}`}>
                      {stats.net_pnl >= 0 ? "+" : ""}${stats.net_pnl.toFixed(2)}
                    </td>
                    <td className="text-right px-2 py-1.5 font-mono">
                      {(stats.avg_edge * 100).toFixed(1)}c
                    </td>
                    <td className="text-right px-2 py-1.5 font-mono text-red/70">
                      ${stats.avg_fee_drag.toFixed(2)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Alpha Attribution */}
      {data.alpha_attribution && Object.keys(data.alpha_attribution).length > 0 && (
        <div className="bg-bg rounded border border-border">
          <div className="text-[10px] text-text-secondary uppercase tracking-wider px-3 py-2 border-b border-border">
            Alpha Attribution (Information Ratio)
          </div>
          <div className="p-3 space-y-2">
            {Object.entries(data.alpha_attribution).map(([source, stats]) => {
              const barWidth = Math.min(Math.abs(stats.ir) * 50, 100);
              const barColor = stats.status === "GOLD" ? "#00d26a" : stats.status === "NOISE" ? "#ff3b3b" : stats.status === "NEGATIVE" ? "#ff3b3b" : "#f59e0b";
              const statusColor = stats.status === "GOLD" ? "bg-green/20 text-green" : stats.status === "NOISE" ? "bg-red/20 text-red" : stats.status === "NEGATIVE" ? "bg-red/20 text-red" : "bg-amber/20 text-amber";
              return (
                <div key={source} className="flex items-center gap-2">
                  <span className="text-[10px] w-20 font-mono text-text-secondary">{source}</span>
                  <div className="flex-1 h-3 bg-surface rounded-full overflow-hidden">
                    <div className="h-full rounded-full" style={{ width: `${barWidth}%`, background: barColor }} />
                  </div>
                  <span className="text-[10px] font-mono w-12 text-right">{stats.ir.toFixed(2)}</span>
                  <span className={`text-[8px] px-1.5 py-0.5 rounded font-mono ${statusColor}`}>{stats.status}</span>
                  <span className={`text-[9px] font-mono w-16 text-right ${stats.cumulative_pnl >= 0 ? "text-green" : "text-red"}`}>
                    ${stats.cumulative_pnl.toFixed(0)}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Trade log */}
      {data.trades.length > 0 && (
        <div className="bg-bg rounded border border-border">
          <div className="text-[10px] text-text-secondary uppercase tracking-wider px-3 py-2 border-b border-border">
            Trade Log ({data.trades.length} trades)
          </div>
          <div className="max-h-48 overflow-y-auto">
            <table className="w-full text-[10px]">
              <thead className="sticky top-0 bg-bg">
                <tr className="text-text-secondary border-b border-border">
                  <th className="text-left px-3 py-1">Ticker</th>
                  <th className="text-center px-2 py-1">Dir</th>
                  <th className="text-right px-2 py-1">Qty</th>
                  <th className="text-right px-2 py-1">Entry</th>
                  <th className="text-right px-2 py-1">Exit</th>
                  <th className="text-right px-2 py-1">Gross</th>
                  <th className="text-right px-2 py-1">Fee</th>
                  <th className="text-right px-2 py-1">Net</th>
                  <th className="text-left px-2 py-1">Exit</th>
                </tr>
              </thead>
              <tbody>
                {data.trades.slice(0, 50).map((t, i) => {
                  const net = t.net_pnl ?? t.pnl;
                  const fee = t.fee ?? 0;
                  return (
                    <tr key={i} className={`border-b border-border/30 ${i % 2 === 1 ? "bg-surface/50" : ""}`}>
                      <td className="px-3 py-1 font-mono truncate max-w-[130px]" title={t.ticker}>
                        {t.ticker.length > 20 ? t.ticker.slice(0, 20) + "\u2026" : t.ticker}
                      </td>
                      <td className={`text-center px-2 py-1 font-bold ${t.direction === "BUY_YES" ? "text-green" : "text-red"}`}>
                        {t.direction === "BUY_YES" ? "YES" : "NO"}
                      </td>
                      <td className="text-right px-2 py-1 font-mono">{t.contracts ?? ""}</td>
                      <td className="text-right px-2 py-1 font-mono">${t.entry_price.toFixed(2)}</td>
                      <td className="text-right px-2 py-1 font-mono">${t.exit_price.toFixed(2)}</td>
                      <td className={`text-right px-2 py-1 font-mono ${t.pnl >= 0 ? "text-green/70" : "text-red/70"}`}>
                        {t.pnl >= 0 ? "+" : ""}${t.pnl.toFixed(0)}
                      </td>
                      <td className="text-right px-2 py-1 font-mono text-red/50">
                        {fee > 0 ? `-${fee.toFixed(0)}` : ""}
                      </td>
                      <td className={`text-right px-2 py-1 font-mono font-semibold ${net >= 0 ? "text-green" : "text-red"}`}>
                        {net >= 0 ? "+" : ""}${net.toFixed(0)}
                      </td>
                      <td className="px-2 py-1 text-text-secondary">{t.exit_reason}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

interface RegimePerf {
  trades: number;
  wins: number;
  win_rate: number;
  net_pnl: number;
  avg_fee_drag: number;
  avg_edge: number;
}

interface AlphaSourceStats {
  ir: number;
  cumulative_pnl: number;
  mean_return: number;
  std_return: number;
  trades: number;
  status: string;
}

interface MonteCarloResult {
  prob_positive: number;
  n_resamples: number;
  n_trades: number;
  bands: Record<string, number[]>;
  final_percentiles: Record<string, number>;
}

interface BacktestResult {
  total_trades: number;
  win_rate: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  max_drawdown: number;
  profit_factor: number;
  avg_win_loss_ratio: number;
  avg_hold_hours: number;
  final_pnl: number;
  gross_pnl: number;
  total_fees: number;
  total_return: number;
  test_period_days: number;
  unique_underlyings: number;
  confidence_note: string;
  regime_performance: Record<string, RegimePerf>;
  alpha_attribution?: Record<string, AlphaSourceStats>;
  equity_curve: { ts: string; equity: number }[];
  monte_carlo?: MonteCarloResult;
  trades: { ticker: string; direction: string; entry_price: number; exit_price: number; contracts: number; pnl: number; net_pnl: number; fee: number; exit_reason: string; regime: string; edge_at_entry: number }[];
}

function PnLChart({ curve, monteCarlo }: { curve: { ts: string; equity: number }[]; monteCarlo?: MonteCarloResult }) {
  if (curve.length < 2) return null;

  const values = curve.map((p) => p.equity);
  const bands = monteCarlo?.bands;
  const hasBands = bands && bands["5"] && bands["95"] && bands["5"].length === curve.length;

  // Compute min/max including bands
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (hasBands) {
    min = Math.min(min, ...bands["5"]);
    max = Math.max(max, ...bands["95"]);
  }
  const range = max - min || 1;

  const W = 720;
  const H = 200;
  const PAD = 30;

  const toX = (i: number) => PAD + (i / (curve.length - 1)) * (W - PAD * 2);
  const toY = (v: number) => H - PAD - ((v - min) / range) * (H - PAD * 2);

  const points = curve.map((p, i) => ({ x: toX(i), y: toY(p.equity), equity: p.equity }));

  const pathD = points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" ");

  // Build band area paths (5-95 outer, 25-75 inner)
  const makeBandPath = (lower: number[], upper: number[]) => {
    const topPoints = lower.map((v, i) => `${i === 0 ? "M" : "L"} ${toX(i).toFixed(1)} ${toY(v).toFixed(1)}`).join(" ");
    const botPoints = [...upper].reverse().map((v, i) => {
      const idx = upper.length - 1 - i;
      return `L ${toX(idx).toFixed(1)} ${toY(v).toFixed(1)}`;
    }).join(" ");
    return `${topPoints} ${botPoints} Z`;
  };

  // Zero line position
  const zeroY = toY(0);

  const finalValue = values[values.length - 1];
  const lineColor = finalValue >= 0 ? "#00d26a" : "#ff3b3b";

  // Median line from bootstrap
  const medianPath = hasBands && bands["50"]
    ? bands["50"].map((v, i) => `${i === 0 ? "M" : "L"} ${toX(i).toFixed(1)} ${toY(v).toFixed(1)}`).join(" ")
    : null;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 200 }}>
      {/* Grid lines */}
      {[0.25, 0.5, 0.75].map((pct) => {
        const y = PAD + pct * (H - PAD * 2);
        const val = max - pct * range;
        return (
          <g key={pct}>
            <line x1={PAD} y1={y} x2={W - PAD} y2={y} stroke="#1e1e2e" strokeWidth="1" />
            <text x={PAD - 4} y={y + 3} textAnchor="end" fill="#888899" fontSize="8" fontFamily="JetBrains Mono">
              ${val.toFixed(0)}
            </text>
          </g>
        );
      })}

      {/* Zero line */}
      {min < 0 && max > 0 && (
        <line x1={PAD} y1={zeroY} x2={W - PAD} y2={zeroY} stroke="#888899" strokeWidth="1" strokeDasharray="4 2" />
      )}

      {/* Monte Carlo confidence bands */}
      {hasBands && (
        <>
          {/* 5-95% outer band */}
          <path d={makeBandPath(bands["95"], bands["5"])} fill="#3b82f6" opacity="0.08" />
          {/* 25-75% inner band */}
          {bands["25"] && bands["75"] && (
            <path d={makeBandPath(bands["75"], bands["25"])} fill="#3b82f6" opacity="0.15" />
          )}
          {/* Median line */}
          {medianPath && (
            <path d={medianPath} fill="none" stroke="#3b82f6" strokeWidth="1" strokeDasharray="3 3" opacity="0.5" />
          )}
        </>
      )}

      {/* Actual P&L line */}
      <path d={pathD} fill="none" stroke={lineColor} strokeWidth="2" />

      {/* Fill below the actual line */}
      <path
        d={`${pathD} L ${points[points.length - 1].x.toFixed(1)} ${(H - PAD).toFixed(1)} L ${PAD.toFixed(1)} ${(H - PAD).toFixed(1)} Z`}
        fill={lineColor}
        opacity="0.1"
      />
    </svg>
  );
}
