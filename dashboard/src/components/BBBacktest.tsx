"use client";

import { useState, useEffect, useCallback } from "react";
import type { RegimePerformance, AlphaAttribution } from "@/lib/types";

const STARTING_CAPITAL = 10000;

interface MonteCarloResult {
  prob_positive: number;
  cluster_prob_positive?: number;
  effective_n?: number;
  largest_cluster_pct?: number;
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
  fee_efficiency: number;
  test_period_days: number;
  unique_underlyings: number;
  confidence_note: string;
  regime_performance: Record<string, RegimePerformance>;
  alpha_attribution?: Record<string, AlphaAttribution>;
  equity_curve: { ts: string; equity: number }[];
  monte_carlo?: MonteCarloResult;
  trades: {
    ticker: string;
    direction: string;
    entry_price: number;
    exit_price: number;
    contracts: number;
    pnl: number;
    net_pnl: number;
    fee: number;
    exit_reason: string;
    regime: string;
    edge_at_entry: number;
  }[];
}

export default function BBBacktest() {
  const [data, setData] = useState<BacktestResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const fetchBacktest = useCallback((force = false) => {
    setLoading(true);
    setError("");
    const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    const url = force ? `${API_BASE}/api/backtest/run` : `${API_BASE}/api/backtest`;
    const opts = force ? { method: "POST" } : {};
    fetch(url, opts)
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

  useEffect(() => {
    fetchBacktest(false);  // load cached on mount
  }, [fetchBacktest]);

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center bg-bb-black">
        <div className="text-center">
          <div className="text-bb-orange font-mono text-sm mb-2">BACKTEST ENGINE</div>
          <div className="text-bb-dim font-mono text-xs">Running walk-forward backtest on historical data...</div>
          <div className="mt-4 flex justify-center gap-1">
            {[0, 1, 2].map((i) => (
              <div
                key={i}
                className="w-2 h-2 bg-bb-orange rounded-full animate-pulse"
                style={{ animationDelay: `${i * 200}ms` }}
              />
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="h-full flex items-center justify-center bg-bb-black">
        <div className="text-center">
          <div className="text-bb-red font-mono text-sm mb-2">BACKTEST FAILED</div>
          <div className="text-bb-dim font-mono text-xs">{error || "No data returned"}</div>
          <button
            onClick={() => fetchBacktest(false)}
            className="mt-4 px-3 py-1 border border-bb-border text-bb-orange font-mono text-xs hover:bg-bb-orange/10 transition-colors"
          >
            RETRY
          </button>
        </div>
      </div>
    );
  }

  const grossPnl = data.gross_pnl ?? data.final_pnl;
  const totalFees = data.total_fees ?? 0;
  const netPnl = data.final_pnl;
  const sortino = data.sortino_ratio ?? 0;
  const holdHrs = data.avg_hold_hours ?? 0;
  const feeEff = data.fee_efficiency ?? (grossPnl !== 0 ? netPnl / grossPnl : 0);
  const testDays = data.test_period_days ?? 1;
  const annualized = (netPnl / STARTING_CAPITAL) / (testDays / 365) * 100;
  const clusterProb = data.monte_carlo?.cluster_prob_positive ?? data.monte_carlo?.prob_positive ?? 0;

  return (
    <div className="h-full overflow-y-auto bg-bb-black font-mono">
      {/* Header bar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-bb-border bg-bb-header sticky top-0 z-10">
        <div className="flex items-center gap-3">
          <span className="text-bb-orange font-bold text-[13px]">BACKTEST</span>
          <span className="text-bb-dim text-[11px] ml-2">Walk-Forward Backtester</span>
        </div>
        <div className="flex items-center gap-3">
          {totalFees > 0 && (
            <span className="text-[10px] text-bb-dim">
              Gross: <span className={grossPnl >= 0 ? "text-bb-green" : "text-bb-red"}>${grossPnl.toFixed(2)}</span>
              {" | "}Fees: <span className="text-bb-red">-${totalFees.toFixed(2)}</span>
              {" | "}Period: <span className="text-bb-white">{testDays.toFixed(1)}d</span>
              {" | "}Underlyings: <span className="text-bb-white">{data.unique_underlyings}</span>
            </span>
          )}
          <button
            onClick={() => fetchBacktest(true)}
            className="px-3 py-1 border border-bb-orange text-bb-orange text-[12px] font-semibold hover:bg-bb-orange/10 transition-colors"
          >
            RUN FRESH BACKTEST
          </button>
        </div>
      </div>

      <div className="p-3 space-y-3">
        {/* Strategy bullets */}
        <div className="bg-bb-panel border border-bb-border px-4 py-2.5 space-y-1 text-[11px] text-bb-dim">
          <div className="flex items-start gap-2">
            <span className="text-bb-blue shrink-0">&#x2022;</span>
            <span>Finds markets where price diverges from fair value by more than transaction costs.</span>
          </div>
          <div className="flex items-start gap-2">
            <span className="text-bb-blue shrink-0">&#x2022;</span>
            <span>Primary alpha: convergence trading near expiry with fee-aware Kelly sizing.</span>
          </div>
          <div className="flex items-start gap-2">
            <span className="text-bb-blue shrink-0">&#x2022;</span>
            <span>
              Generated <span className="text-bb-green font-bold">+${netPnl.toFixed(0)}</span> net on{" "}
              <span className="text-bb-white font-bold">{data.total_trades}</span> trades.{" "}
              <span className="text-bb-white font-bold">{(clusterProb * 100).toFixed(0)}%</span> probability of profit
              (cluster-adjusted Monte Carlo).
            </span>
          </div>
        </div>

        {/* Primary metrics row */}
        <div className="grid grid-cols-5 gap-px bg-bb-border">
          {[
            {
              label: "TRADES",
              value: `${data.total_trades}`,
              sub: data.monte_carlo?.effective_n ? `${data.monte_carlo.effective_n} clusters` : `${data.unique_underlyings} mkts`,
              color: "text-bb-white",
            },
            {
              label: "WIN RATE",
              value: `${(data.win_rate * 100).toFixed(1)}%`,
              sub: `${Math.round(data.win_rate * data.total_trades)}W / ${Math.round((1 - data.win_rate) * data.total_trades)}L`,
              color: data.win_rate > 0.5 ? "text-bb-green" : "text-bb-red",
            },
            {
              label: "SHARPE",
              value: data.sharpe_ratio.toFixed(2),
              sub: "per-trade risk-adj",
              color: data.sharpe_ratio > 0.5 ? "text-bb-green" : data.sharpe_ratio > 0 ? "text-bb-orange" : "text-bb-red",
            },
            {
              label: "NET P&L",
              value: `${netPnl >= 0 ? "+" : ""}$${netPnl.toFixed(0)}`,
              sub: `${(data.total_return * 100).toFixed(1)}% return`,
              color: netPnl >= 0 ? "text-bb-green" : "text-bb-red",
            },
            {
              label: "P(PROFIT)",
              value: `${(clusterProb * 100).toFixed(0)}%`,
              sub: "cluster-adj MC",
              color: clusterProb >= 0.6 ? "text-bb-green" : clusterProb >= 0.4 ? "text-bb-orange" : "text-bb-red",
            },
          ].map((m) => (
            <div key={m.label} className="bg-bb-panel px-4 py-3 text-center">
              <div className="text-[9px] text-bb-dim uppercase tracking-widest mb-1">{m.label}</div>
              <div className={`text-xl font-bold ${m.color}`}>{m.value}</div>
              <div className="text-[9px] text-bb-dim mt-0.5">{m.sub}</div>
            </div>
          ))}
        </div>

        {/* Secondary metrics row */}
        <div className="grid grid-cols-8 gap-px bg-bb-border">
          {[
            { label: "Sortino", value: sortino.toFixed(2), color: sortino > 0.5 ? "text-bb-green" : sortino > 0 ? "text-bb-orange" : "text-bb-red" },
            { label: "Max DD", value: `${(data.max_drawdown * 100).toFixed(1)}%`, color: "text-bb-red" },
            { label: "Profit Factor", value: data.profit_factor.toFixed(2), color: data.profit_factor > 1 ? "text-bb-green" : "text-bb-red" },
            { label: "Avg W/L", value: data.avg_win_loss_ratio.toFixed(2), color: data.avg_win_loss_ratio > 1 ? "text-bb-green" : "text-bb-red" },
            { label: "Avg Hold", value: holdHrs > 0 ? `${holdHrs.toFixed(1)}h` : "N/A", color: "text-bb-white" },
            { label: "Fee Eff", value: `${(feeEff * 100).toFixed(0)}%`, color: feeEff > 0.3 ? "text-bb-green" : feeEff > 0.1 ? "text-bb-orange" : "text-bb-red" },
            { label: "Return", value: `${(data.total_return * 100).toFixed(1)}%`, color: data.total_return > 0 ? "text-bb-green" : "text-bb-red" },
            { label: "Annualized", value: `~${annualized.toFixed(0)}%`, color: annualized > 0 ? "text-bb-green" : "text-bb-red" },
          ].map((m) => (
            <div key={m.label} className="bg-bb-panel px-2 py-2 text-center">
              <div className="text-[8px] text-bb-dim uppercase tracking-wider">{m.label}</div>
              <div className={`text-[13px] font-bold ${m.color}`}>{m.value}</div>
            </div>
          ))}
        </div>

        {/* Equity Curve + Monte Carlo side by side */}
        <div className="grid grid-cols-3 gap-3">
          {/* Equity curve — 2 cols */}
          <div className="col-span-2 bg-bb-panel border border-bb-border p-3">
            <div className="flex items-center justify-between mb-2">
              <div className="text-[10px] text-bb-orange uppercase tracking-wider font-bold">EQUITY CURVE</div>
              {data.monte_carlo?.bands && Object.keys(data.monte_carlo.bands).length > 0 && (
                <div className="flex items-center gap-3 text-[8px] text-bb-dim">
                  <span className="flex items-center gap-1">
                    <span className="inline-block w-3 h-1.5 rounded-sm bg-bb-blue/10" />5-95%
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="inline-block w-3 h-1.5 rounded-sm bg-bb-blue/25" />25-75%
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="inline-block w-3 h-0.5 bg-bb-blue/50" />Median
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="inline-block w-3 h-0.5 bg-bb-green" />Actual
                  </span>
                </div>
              )}
            </div>
            {data.equity_curve.length > 1 ? (
              <PnLChart curve={data.equity_curve} monteCarlo={data.monte_carlo} />
            ) : (
              <div className="h-[220px] flex items-center justify-center text-bb-dim text-[10px]">
                Insufficient data for equity curve
              </div>
            )}
          </div>

          {/* Monte Carlo stats — 1 col */}
          <div className="bg-bb-panel border border-bb-border p-3">
            <div className="text-[10px] text-bb-orange uppercase tracking-wider font-bold mb-3">MONTE CARLO</div>
            {data.monte_carlo && data.monte_carlo.prob_positive > 0 ? (
              <MonteCarloStats mc={data.monte_carlo} />
            ) : (
              <div className="h-full flex items-center justify-center text-bb-dim text-[10px]">
                No Monte Carlo data available
              </div>
            )}
          </div>
        </div>

        {/* Confidence note */}
        {data.confidence_note && (
          <div className="text-[9px] text-bb-orange/80 bg-bb-orange/5 border border-bb-orange/20 px-3 py-1.5 font-mono">
            {data.confidence_note}
          </div>
        )}

        {/* Regime Performance */}
        {data.regime_performance && Object.keys(data.regime_performance).length > 0 && (
          <div className="bg-bb-panel border border-bb-border">
            <div className="text-[10px] text-bb-orange uppercase tracking-wider font-bold px-3 py-2 border-b border-bb-border">
              REGIME PERFORMANCE
            </div>
            <table className="w-full text-[10px]">
              <thead>
                <tr className="text-bb-dim border-b border-bb-border">
                  <th className="text-left px-3 py-1.5 font-normal">Regime</th>
                  <th className="text-center px-2 py-1.5 font-normal">Trades</th>
                  <th className="text-center px-2 py-1.5 font-normal">Win Rate</th>
                  <th className="text-right px-2 py-1.5 font-normal">Net P&L</th>
                  <th className="text-right px-2 py-1.5 font-normal">Avg Edge</th>
                  <th className="text-right px-2 py-1.5 font-normal">Avg Fee Drag</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(data.regime_performance).map(([regime, stats]) => {
                  const wrColor = stats.win_rate > 0.6 ? "text-bb-green" : stats.win_rate > 0.4 ? "text-bb-orange" : "text-bb-red";
                  return (
                    <tr key={regime} className="border-b border-bb-border/30 hover:bg-bb-selected/30">
                      <td className="px-3 py-1.5 font-bold text-bb-white">{regime}</td>
                      <td className="text-center px-2 py-1.5">{stats.trades}</td>
                      <td className={`text-center px-2 py-1.5 font-bold ${wrColor}`}>
                        {(stats.win_rate * 100).toFixed(1)}%
                      </td>
                      <td className={`text-right px-2 py-1.5 font-bold ${stats.net_pnl >= 0 ? "text-bb-green" : "text-bb-red"}`}>
                        {stats.net_pnl >= 0 ? "+" : ""}${stats.net_pnl.toFixed(2)}
                      </td>
                      <td className="text-right px-2 py-1.5 text-bb-dim">
                        {(stats.avg_edge * 100).toFixed(1)}c
                      </td>
                      <td className="text-right px-2 py-1.5 text-bb-red/70">
                        ${stats.avg_fee_drag.toFixed(2)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Alpha Attribution + Trade Log side by side */}
        <div className="grid grid-cols-3 gap-3">
          {/* Alpha Attribution */}
          <div className="bg-bb-panel border border-bb-border">
            <div className="text-[10px] text-bb-orange uppercase tracking-wider font-bold px-3 py-2 border-b border-bb-border">
              ALPHA ATTRIBUTION (IR)
            </div>
            {data.alpha_attribution && Object.keys(data.alpha_attribution).length > 0 ? (
              <div className="p-3 space-y-2">
                {Object.entries(data.alpha_attribution).map(([source, stats]) => {
                  const barWidth = Math.min(Math.abs(stats.ir) * 50, 100);
                  const barColor =
                    stats.status === "GOLD"
                      ? "#00ff00"
                      : stats.status === "NOISE" || stats.status === "NEGATIVE"
                      ? "#ff0000"
                      : "#ff6600";
                  const statusColor =
                    stats.status === "GOLD"
                      ? "bg-bb-green/20 text-bb-green"
                      : stats.status === "NOISE" || stats.status === "NEGATIVE"
                      ? "bg-bb-red/20 text-bb-red"
                      : "bg-bb-orange/20 text-bb-orange";
                  return (
                    <div key={source} className="flex items-center gap-2">
                      <span className="text-[10px] w-20 text-bb-dim truncate" title={source}>{source}</span>
                      <div className="flex-1 h-3 bg-bb-black rounded-full overflow-hidden">
                        <div className="h-full rounded-full" style={{ width: `${barWidth}%`, background: barColor }} />
                      </div>
                      <span className="text-[10px] w-10 text-right text-bb-white">{stats.ir.toFixed(2)}</span>
                      <span className={`text-[8px] px-1.5 py-0.5 rounded ${statusColor}`}>{stats.status}</span>
                      <span className={`text-[9px] w-14 text-right ${stats.cumulative_pnl >= 0 ? "text-bb-green" : "text-bb-red"}`}>
                        ${stats.cumulative_pnl.toFixed(0)}
                      </span>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="p-4 text-bb-dim text-[10px] text-center">No attribution data</div>
            )}
          </div>

          {/* Trade Log — 2 cols */}
          <div className="col-span-2 bg-bb-panel border border-bb-border">
            <div className="text-[10px] text-bb-orange uppercase tracking-wider font-bold px-3 py-2 border-b border-bb-border">
              TRADE LOG ({data.trades.length} trades)
            </div>
            <div className="max-h-[280px] overflow-y-auto">
              <table className="w-full text-[10px]">
                <thead className="sticky top-0 bg-bb-panel">
                  <tr className="text-bb-dim border-b border-bb-border">
                    <th className="text-left px-3 py-1 font-normal">Ticker</th>
                    <th className="text-center px-2 py-1 font-normal">Dir</th>
                    <th className="text-right px-2 py-1 font-normal">Qty</th>
                    <th className="text-right px-2 py-1 font-normal">Entry</th>
                    <th className="text-right px-2 py-1 font-normal">Exit</th>
                    <th className="text-right px-2 py-1 font-normal">Gross</th>
                    <th className="text-right px-2 py-1 font-normal">Fee</th>
                    <th className="text-right px-2 py-1 font-normal">Net</th>
                    <th className="text-left px-2 py-1 font-normal">Regime</th>
                    <th className="text-right px-2 py-1 font-normal">Edge</th>
                    <th className="text-left px-2 py-1 font-normal">Exit Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {data.trades.slice(0, 100).map((t, i) => {
                    const net = t.net_pnl ?? t.pnl;
                    const fee = t.fee ?? 0;
                    const isWinner = net > 0;
                    return (
                      <tr
                        key={i}
                        className={`border-b border-bb-border/20 hover:bg-bb-selected/30 ${
                          isWinner ? "bg-bb-green/[0.03]" : net < 0 ? "bg-bb-red/[0.03]" : ""
                        }`}
                      >
                        <td className="px-3 py-1 truncate max-w-[140px] text-bb-white" title={t.ticker}>
                          {t.ticker.length > 22 ? t.ticker.slice(0, 22) + "\u2026" : t.ticker}
                        </td>
                        <td className={`text-center px-2 py-1 font-bold ${t.direction === "BUY_YES" ? "text-bb-green" : "text-bb-red"}`}>
                          {t.direction === "BUY_YES" ? "YES" : "NO"}
                        </td>
                        <td className="text-right px-2 py-1 text-bb-dim">{t.contracts ?? ""}</td>
                        <td className="text-right px-2 py-1">${t.entry_price.toFixed(2)}</td>
                        <td className="text-right px-2 py-1">${t.exit_price.toFixed(2)}</td>
                        <td className={`text-right px-2 py-1 ${t.pnl >= 0 ? "text-bb-green/70" : "text-bb-red/70"}`}>
                          {t.pnl >= 0 ? "+" : ""}${t.pnl.toFixed(0)}
                        </td>
                        <td className="text-right px-2 py-1 text-bb-red/50">
                          {fee > 0 ? `-${fee.toFixed(0)}` : ""}
                        </td>
                        <td className={`text-right px-2 py-1 font-bold ${net >= 0 ? "text-bb-green" : "text-bb-red"}`}>
                          {net >= 0 ? "+" : ""}${net.toFixed(0)}
                        </td>
                        <td className="px-2 py-1 text-bb-dim">{t.regime}</td>
                        <td className="text-right px-2 py-1 text-bb-dim">{(t.edge_at_entry * 100).toFixed(1)}c</td>
                        <td className="px-2 py-1 text-bb-dim">{t.exit_reason}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* Annualized context footer */}
        <div className="text-[9px] text-bb-dim px-1 pb-2">
          Annualized: <span className="text-bb-white font-bold">~{annualized.toFixed(0)}%</span>
          <span className="ml-3 opacity-60">S&amp;P 500 avg ~10% | Risk-free ~5%</span>
          <span className="ml-3 opacity-60">|</span>
          <span className="ml-3 opacity-60">{data.monte_carlo?.n_resamples?.toLocaleString() ?? 0} bootstrap resamples</span>
        </div>
      </div>
    </div>
  );
}

/* ── Monte Carlo Stats Panel ─────────────────────────────────────────────── */

function MonteCarloStats({ mc }: { mc: MonteCarloResult }) {
  const clusterProb = mc.cluster_prob_positive ?? mc.prob_positive;
  const effectiveN = mc.effective_n ?? mc.n_trades;
  const probColor = clusterProb >= 0.6 ? "text-bb-green" : clusterProb >= 0.4 ? "text-bb-orange" : "text-bb-red";
  const probBg = clusterProb >= 0.6 ? "bg-bb-green/5 border-bb-green/30" : clusterProb >= 0.4 ? "bg-bb-orange/5 border-bb-orange/30" : "bg-bb-red/5 border-bb-red/30";

  return (
    <div className="space-y-3">
      {/* Big probability display */}
      <div className={`text-center py-3 border rounded ${probBg}`}>
        <div className={`text-3xl font-bold ${probColor}`}>{(clusterProb * 100).toFixed(1)}%</div>
        <div className="text-[9px] text-bb-dim mt-1">P(profit) cluster-adjusted</div>
      </div>

      {/* Trade-level vs cluster */}
      <div className="space-y-1.5 text-[10px]">
        <div className="flex justify-between">
          <span className="text-bb-dim">Trade-level P(profit)</span>
          <span className="text-bb-white font-bold">{(mc.prob_positive * 100).toFixed(1)}%</span>
        </div>
        {effectiveN < mc.n_trades && (
          <div className="flex justify-between">
            <span className="text-bb-dim">Independent clusters</span>
            <span className="text-bb-white font-bold">{effectiveN}</span>
          </div>
        )}
        <div className="flex justify-between">
          <span className="text-bb-dim">Total trades</span>
          <span className="text-bb-white">{mc.n_trades}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-bb-dim">Resamples</span>
          <span className="text-bb-white">{mc.n_resamples.toLocaleString()}</span>
        </div>
        {mc.largest_cluster_pct != null && (
          <div className="flex justify-between">
            <span className="text-bb-dim">Largest cluster</span>
            <span className="text-bb-white">{(mc.largest_cluster_pct * 100).toFixed(0)}%</span>
          </div>
        )}
      </div>

      {/* Final percentiles */}
      {mc.final_percentiles && Object.keys(mc.final_percentiles).length > 0 && (
        <div className="border-t border-bb-border pt-2 space-y-1.5 text-[10px]">
          <div className="text-[9px] text-bb-orange uppercase tracking-wider mb-1">PERCENTILES</div>
          {["5", "25", "50", "75", "95"].map((pct) => {
            const val = mc.final_percentiles[pct];
            if (val == null) return null;
            const color = val >= 0 ? "text-bb-green" : "text-bb-red";
            const isMedian = pct === "50";
            return (
              <div key={pct} className="flex justify-between">
                <span className={`text-bb-dim ${isMedian ? "font-bold" : ""}`}>
                  {isMedian ? "Median" : `${pct}th`}
                </span>
                <span className={`${color} ${isMedian ? "font-bold" : ""}`}>
                  ${val.toFixed(0)}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ── PnL Chart (SVG) ─────────────────────────────────────────────────────── */

function PnLChart({ curve, monteCarlo }: { curve: { ts: string; equity: number }[]; monteCarlo?: MonteCarloResult }) {
  if (curve.length < 2) return null;

  const values = curve.map((p) => p.equity);
  const bands = monteCarlo?.bands;
  const hasBands = bands && bands["5"] && bands["95"] && bands["5"].length === curve.length;

  let min = Math.min(...values);
  let max = Math.max(...values);
  if (hasBands) {
    min = Math.min(min, ...bands["5"]);
    max = Math.max(max, ...bands["95"]);
  }
  const range = max - min || 1;

  const W = 900;
  const H = 220;
  const PAD = 35;

  const toX = (i: number) => PAD + (i / (curve.length - 1)) * (W - PAD * 2);
  const toY = (v: number) => H - PAD - ((v - min) / range) * (H - PAD * 2);

  const points = curve.map((p, i) => ({ x: toX(i), y: toY(p.equity), equity: p.equity }));
  const pathD = points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" ");

  const makeBandPath = (lower: number[], upper: number[]) => {
    const topPoints = lower.map((v, i) => `${i === 0 ? "M" : "L"} ${toX(i).toFixed(1)} ${toY(v).toFixed(1)}`).join(" ");
    const botPoints = [...upper]
      .reverse()
      .map((v, i) => {
        const idx = upper.length - 1 - i;
        return `L ${toX(idx).toFixed(1)} ${toY(v).toFixed(1)}`;
      })
      .join(" ");
    return `${topPoints} ${botPoints} Z`;
  };

  const zeroY = toY(0);
  const finalValue = values[values.length - 1];
  const lineColor = finalValue >= 0 ? "#00ff00" : "#ff0000";

  const medianPath =
    hasBands && bands["50"]
      ? bands["50"].map((v, i) => `${i === 0 ? "M" : "L"} ${toX(i).toFixed(1)} ${toY(v).toFixed(1)}`).join(" ")
      : null;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 220 }}>
      {/* Grid lines */}
      {[0, 0.25, 0.5, 0.75, 1].map((pct) => {
        const y = PAD + pct * (H - PAD * 2);
        const val = max - pct * range;
        return (
          <g key={pct}>
            <line x1={PAD} y1={y} x2={W - PAD} y2={y} stroke="#1a1a1a" strokeWidth="1" />
            <text x={PAD - 4} y={y + 3} textAnchor="end" fill="#888888" fontSize="8" fontFamily="monospace">
              ${val.toFixed(0)}
            </text>
          </g>
        );
      })}

      {/* Zero line */}
      {min < 0 && max > 0 && (
        <line x1={PAD} y1={zeroY} x2={W - PAD} y2={zeroY} stroke="#888888" strokeWidth="1" strokeDasharray="4 2" />
      )}

      {/* MC confidence bands */}
      {hasBands && (
        <>
          <path d={makeBandPath(bands["95"], bands["5"])} fill="#00aaff" opacity="0.06" />
          {bands["25"] && bands["75"] && (
            <path d={makeBandPath(bands["75"], bands["25"])} fill="#00aaff" opacity="0.12" />
          )}
          {medianPath && (
            <path d={medianPath} fill="none" stroke="#00aaff" strokeWidth="1" strokeDasharray="3 3" opacity="0.4" />
          )}
        </>
      )}

      {/* Actual P&L line */}
      <path d={pathD} fill="none" stroke={lineColor} strokeWidth="2" />

      {/* Fill under */}
      <path
        d={`${pathD} L ${points[points.length - 1].x.toFixed(1)} ${(H - PAD).toFixed(1)} L ${PAD.toFixed(1)} ${(H - PAD).toFixed(1)} Z`}
        fill={lineColor}
        opacity="0.08"
      />

      {/* Final value label */}
      <text
        x={points[points.length - 1].x + 4}
        y={points[points.length - 1].y + 3}
        fill={lineColor}
        fontSize="9"
        fontFamily="monospace"
        fontWeight="bold"
      >
        ${finalValue.toFixed(0)}
      </text>
    </svg>
  );
}
