"use client";

import { useDashboard } from "@/lib/store";
import { api } from "@/lib/api";
import { useState, useEffect, useMemo } from "react";
import type { CorrelationData, ArbitrageOpportunity, Signal } from "@/lib/types";

// ── Helpers ─────────────────────────────────────────────────────────────────

function clamp(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v));
}

function edgeColor(edge: number): string {
  if (edge > 0.02) return "text-green-400";
  if (edge < -0.02) return "text-red-400";
  return "text-[#888899]";
}

function dirColor(dir: string): string {
  if (dir === "BUY_YES") return "text-green-400";
  if (dir === "BUY_NO") return "text-red-400";
  return "text-[#888899]";
}

function dirLabel(dir: string): string {
  if (dir === "BUY_YES") return "YES";
  if (dir === "BUY_NO") return "NO";
  return "HOLD";
}

const REGIME_COLORS: Record<string, string> = {
  CONVERGENCE: "bg-blue-500/20 text-blue-400 border-blue-500/40",
  TRENDING: "bg-green-500/20 text-green-400 border-green-500/40",
  MEAN_REVERTING: "bg-amber-500/20 text-amber-400 border-amber-500/40",
  HIGH_VOLATILITY: "bg-red-500/20 text-red-400 border-red-500/40",
  STALE: "bg-gray-500/20 text-gray-400 border-gray-500/40",
};

const REGIME_BAR_COLORS: Record<string, string> = {
  CONVERGENCE: "bg-blue-500",
  TRENDING: "bg-green-500",
  MEAN_REVERTING: "bg-amber-500",
  HIGH_VOLATILITY: "bg-red-500",
  STALE: "bg-gray-500",
};

function regimeBadge(regime: string) {
  const cls = REGIME_COLORS[regime] || REGIME_COLORS.STALE;
  const short = regime === "MEAN_REVERTING" ? "MEAN_REV" : regime === "HIGH_VOLATILITY" ? "HIGH_VOL" : regime === "CONVERGENCE" ? "CONV" : regime;
  return (
    <span className={`px-1 py-0.5 rounded text-[9px] font-mono border ${cls}`}>
      {short}
    </span>
  );
}

/** Count how many signal sources agree: fair_value, price_predictor, sentiment */
function agreementScore(sig: Signal): { agreed: number; total: number } {
  let total = 0;
  let agreed = 0;

  // Fair value: edge > 0 means model agrees with direction
  const fvAgrees = sig.edge > 0;
  total++;
  if (fvAgrees) agreed++;

  // Price predictor: predicted_change sign matches direction
  const predAgrees =
    (sig.direction === "BUY_YES" && sig.predicted_change > 0) ||
    (sig.direction === "BUY_NO" && sig.predicted_change < 0);
  total++;
  if (predAgrees) agreed++;

  // Sentiment: sentiment_edge sign matches direction
  if (sig.sentiment_edge !== undefined && sig.sentiment_edge !== null) {
    const sentAgrees =
      (sig.direction === "BUY_YES" && sig.sentiment_edge > 0) ||
      (sig.direction === "BUY_NO" && sig.sentiment_edge < 0);
    total++;
    if (sentAgrees) agreed++;
  } else {
    // No sentiment data, keep total at 2
  }

  return { agreed, total };
}

// ── Main Component ──────────────────────────────────────────────────────────

export default function BBSentiment() {
  const { signals } = useDashboard();
  const [correlations, setCorrelations] = useState<CorrelationData | null>(null);
  const [arbitrage, setArbitrage] = useState<ArbitrageOpportunity[]>([]);

  useEffect(() => {
    api.getCorrelations().then(setCorrelations).catch(console.error);
    api.getArbitrage().then(setArbitrage).catch(console.error);

    const id = setInterval(() => {
      api.getCorrelations().then(setCorrelations).catch(() => {});
      api.getArbitrage().then(setArbitrage).catch(() => {});
    }, 30000);
    return () => clearInterval(id);
  }, []);

  // Summary stats
  const stats = useMemo(() => {
    const bulls = signals.filter((s) => s.direction === "BUY_YES").length;
    const bears = signals.filter((s) => s.direction === "BUY_NO").length;
    const neutrals = signals.filter((s) => s.direction === "HOLD").length;
    const avgConf =
      signals.length > 0
        ? signals.reduce((a, s) => a + s.confidence, 0) / signals.length
        : 0;
    return { total: signals.length, bulls, bears, neutrals, avgConf };
  }, [signals]);

  // Regime distribution
  const regimeDist = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const s of signals) {
      const r = s.regime || "STALE";
      counts[r] = (counts[r] || 0) + 1;
    }
    const total = signals.length || 1;
    return Object.entries(counts)
      .map(([regime, count]) => ({ regime, count, pct: count / total }))
      .sort((a, b) => b.count - a.count);
  }, [signals]);

  return (
    <div className="h-full flex flex-col overflow-hidden bg-[#0a0a0f]">
      {/* Header */}
      <div className="shrink-0 px-3 py-1.5 border-b border-[#1e1e2e] flex items-center gap-3">
        <span className="text-[#ff8c00] font-bold text-[11px] tracking-wider">
          F5 SENTIMENT
        </span>
        <span className="text-[#888899] text-[10px]">
          Multi-Source Signal Agreement
        </span>
      </div>

      {/* Summary stats row */}
      <SummaryRow stats={stats} />

      {/* Main content: scrollable */}
      <div className="flex-1 min-h-0 overflow-y-auto px-2 pb-2 space-y-1">
        {/* Signal Agreement Table */}
        <SignalAgreementTable signals={signals} />

        {/* Bottom row: Regime + Correlations side by side */}
        <div className="grid grid-cols-2 gap-1">
          <RegimeDistribution data={regimeDist} />
          <CrossMarketCorrelations data={correlations} />
        </div>

        {/* Arbitrage Scanner */}
        <ArbitrageScanner data={arbitrage} />
      </div>
    </div>
  );
}

// ── Summary Row ─────────────────────────────────────────────────────────────

function SummaryRow({
  stats,
}: {
  stats: { total: number; bulls: number; bears: number; neutrals: number; avgConf: number };
}) {
  const cells = [
    { label: "SIGNALS", value: stats.total, color: "text-[#e0e0e0]" },
    { label: "BULL", value: stats.bulls, color: "text-green-400" },
    { label: "BEAR", value: stats.bears, color: "text-red-400" },
    { label: "NEUTRAL", value: stats.neutrals, color: "text-[#888899]" },
    { label: "AVG CONF", value: stats.avgConf.toFixed(2), color: stats.avgConf > 0.6 ? "text-green-400" : stats.avgConf > 0.4 ? "text-amber-400" : "text-red-400" },
  ];

  return (
    <div className="shrink-0 grid grid-cols-5 border-b border-[#1e1e2e]">
      {cells.map((c) => (
        <div key={c.label} className="px-3 py-1.5 text-center border-r border-[#1e1e2e] last:border-r-0">
          <div className="text-[9px] text-[#888899] tracking-wider">{c.label}</div>
          <div className={`text-[14px] font-mono font-bold ${c.color}`}>{c.value}</div>
        </div>
      ))}
    </div>
  );
}

// ── Signal Agreement Table ──────────────────────────────────────────────────

function SignalAgreementTable({ signals }: { signals: Signal[] }) {
  if (signals.length === 0) {
    return (
      <div className="bg-[#12121a] border border-[#1e1e2e] rounded p-3">
        <div className="text-[#ff8c00] text-[10px] font-bold tracking-wider mb-2">
          SIGNAL AGREEMENT
        </div>
        <div className="text-[#888899] text-[10px]">NO SIGNALS AVAILABLE</div>
      </div>
    );
  }

  return (
    <div className="bg-[#12121a] border border-[#1e1e2e] rounded">
      <div className="px-2 py-1 border-b border-[#1e1e2e]">
        <span className="text-[#ff8c00] text-[10px] font-bold tracking-wider">
          SIGNAL AGREEMENT
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[10px]">
          <thead>
            <tr className="text-[#888899] border-b border-[#1e1e2e]">
              <th className="text-left px-2 py-1 font-normal">TICKER</th>
              <th className="text-left px-2 py-1 font-normal">TITLE</th>
              <th className="text-center px-2 py-1 font-normal">DIR</th>
              <th className="text-right px-2 py-1 font-normal">EDGE</th>
              <th className="text-right px-2 py-1 font-normal">CONF</th>
              <th className="text-center px-2 py-1 font-normal">REGIME</th>
              <th className="text-right px-2 py-1 font-normal">SENT.</th>
              <th className="text-center px-2 py-1 font-normal">AGREE</th>
            </tr>
          </thead>
          <tbody>
            {signals.map((sig) => {
              const { agreed, total } = agreementScore(sig);
              const allAgree = agreed === total;
              return (
                <tr
                  key={sig.ticker}
                  className="border-b border-[#1e1e2e]/50 hover:bg-[#1a1a2a] transition-colors"
                >
                  <td className="px-2 py-1 font-mono text-[#e0e0e0] whitespace-nowrap">
                    {sig.ticker.length > 16 ? sig.ticker.slice(0, 16) + "\u2026" : sig.ticker}
                  </td>
                  <td className="px-2 py-1 text-[#888899] max-w-[200px] truncate">
                    {sig.title}
                  </td>
                  <td className={`px-2 py-1 text-center font-mono font-bold ${dirColor(sig.direction)}`}>
                    {dirLabel(sig.direction)}
                  </td>
                  <td className={`px-2 py-1 text-right font-mono ${edgeColor(sig.edge)}`}>
                    {sig.edge >= 0 ? "+" : ""}
                    {(sig.edge * 100).toFixed(1)}c
                  </td>
                  <td className="px-2 py-1 text-right font-mono text-[#e0e0e0]">
                    {sig.confidence.toFixed(2)}
                  </td>
                  <td className="px-2 py-1 text-center">{regimeBadge(sig.regime)}</td>
                  <td className={`px-2 py-1 text-right font-mono ${sig.sentiment_edge != null ? edgeColor(sig.sentiment_edge) : "text-[#555]"}`}>
                    {sig.sentiment_edge != null
                      ? `${sig.sentiment_edge >= 0 ? "+" : ""}${(sig.sentiment_edge * 100).toFixed(1)}c`
                      : "--"}
                  </td>
                  <td className="px-2 py-1 text-center font-mono">
                    <span className={allAgree ? "text-green-400" : agreed >= total / 2 ? "text-amber-400" : "text-red-400"}>
                      {allAgree ? "\u2713" : "\u2717"} {agreed}/{total}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Regime Distribution ─────────────────────────────────────────────────────

function RegimeDistribution({
  data,
}: {
  data: { regime: string; count: number; pct: number }[];
}) {
  return (
    <div className="bg-[#12121a] border border-[#1e1e2e] rounded flex flex-col">
      <div className="px-2 py-1 border-b border-[#1e1e2e]">
        <span className="text-[#ff8c00] text-[10px] font-bold tracking-wider">
          REGIME DISTRIBUTION
        </span>
      </div>
      <div className="p-2 space-y-1.5 flex-1">
        {data.length === 0 && (
          <div className="text-[#888899] text-[10px]">NO DATA</div>
        )}
        {data.map(({ regime, count, pct }) => {
          const barColor = REGIME_BAR_COLORS[regime] || "bg-gray-500";
          const short =
            regime === "MEAN_REVERTING"
              ? "MEAN_REV"
              : regime === "HIGH_VOLATILITY"
              ? "HIGH_VOL"
              : regime === "CONVERGENCE"
              ? "CONV"
              : regime;
          return (
            <div key={regime}>
              <div className="flex items-center justify-between text-[10px] mb-0.5">
                <span className="text-[#e0e0e0] font-mono">{short}</span>
                <span className="text-[#888899] font-mono">
                  {count} ({(pct * 100).toFixed(0)}%)
                </span>
              </div>
              <div className="h-[6px] bg-[#1a1a2a] rounded overflow-hidden">
                <div
                  className={`h-full rounded ${barColor}`}
                  style={{ width: `${clamp(pct * 100, 0, 100)}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Cross-Market Correlations ───────────────────────────────────────────────

function CrossMarketCorrelations({ data }: { data: CorrelationData | null }) {
  if (!data) {
    return (
      <div className="bg-[#12121a] border border-[#1e1e2e] rounded flex flex-col">
        <div className="px-2 py-1 border-b border-[#1e1e2e]">
          <span className="text-[#ff8c00] text-[10px] font-bold tracking-wider">
            CROSS-MARKET CORRELATIONS
          </span>
        </div>
        <div className="p-2 text-[#888899] text-[10px]">LOADING...</div>
      </div>
    );
  }

  // Sort by absolute correlation descending, take top 10
  const topPairs = [...data.matrix]
    .sort((a, b) => Math.abs(b.corr) - Math.abs(a.corr))
    .slice(0, 10);

  function corrColor(c: number): string {
    const abs = Math.abs(c);
    if (abs > 0.8) return "text-red-400";
    if (abs > 0.6) return "text-amber-400";
    if (abs > 0.4) return "text-[#e0e0e0]";
    return "text-[#888899]";
  }

  return (
    <div className="bg-[#12121a] border border-[#1e1e2e] rounded flex flex-col">
      <div className="px-2 py-1 border-b border-[#1e1e2e]">
        <span className="text-[#ff8c00] text-[10px] font-bold tracking-wider">
          CROSS-MARKET CORRELATIONS
        </span>
      </div>
      <div className="p-2 space-y-0.5 flex-1 overflow-y-auto max-h-[200px]">
        {/* Divergence alerts first */}
        {data.divergences.length > 0 && (
          <div className="mb-2">
            {data.divergences.map((d, i) => (
              <div
                key={i}
                className="flex items-center gap-1 text-[10px] py-0.5 px-1 bg-red-500/10 border border-red-500/30 rounded mb-0.5"
              >
                <span className="text-red-400 font-bold text-[9px]">DIVERGE!</span>
                <span className="text-[#e0e0e0] font-mono truncate">
                  {d.t1.length > 12 ? d.t1.slice(0, 12) : d.t1} / {d.t2.length > 12 ? d.t2.slice(0, 12) : d.t2}
                </span>
                <span className="text-amber-400 font-mono ml-auto">
                  r={d.correlation.toFixed(2)}
                </span>
                <span className="text-red-400 font-mono">
                  spr={d.spread.toFixed(2)}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Top correlated pairs */}
        {topPairs.map((p, i) => (
          <div key={i} className="flex items-center text-[10px] font-mono py-0.5">
            <span className="text-[#e0e0e0] truncate flex-1">
              {p.t1.length > 12 ? p.t1.slice(0, 12) : p.t1} / {p.t2.length > 12 ? p.t2.slice(0, 12) : p.t2}
            </span>
            <span className={`ml-2 ${corrColor(p.corr)}`}>
              {p.corr >= 0 ? "+" : ""}
              {p.corr.toFixed(2)}
            </span>
          </div>
        ))}

        {topPairs.length === 0 && data.divergences.length === 0 && (
          <div className="text-[#888899] text-[10px]">NO CORRELATION DATA</div>
        )}
      </div>
    </div>
  );
}

// ── Arbitrage Scanner ───────────────────────────────────────────────────────

function ArbitrageScanner({ data }: { data: ArbitrageOpportunity[] }) {
  return (
    <div className="bg-[#12121a] border border-[#1e1e2e] rounded">
      <div className="px-2 py-1 border-b border-[#1e1e2e] flex items-center gap-2">
        <span className="text-[#ff8c00] text-[10px] font-bold tracking-wider">
          ARBITRAGE SCANNER
        </span>
        {data.length > 0 && (
          <span className="text-[9px] font-mono px-1.5 py-0.5 bg-green-500/20 text-green-400 border border-green-500/40 rounded">
            {data.length} FOUND
          </span>
        )}
      </div>
      <div className="overflow-x-auto">
        {data.length === 0 ? (
          <div className="p-2 text-[#888899] text-[10px]">
            NO ARBITRAGE OPPORTUNITIES DETECTED
          </div>
        ) : (
          <table className="w-full text-[10px]">
            <thead>
              <tr className="text-[#888899] border-b border-[#1e1e2e]">
                <th className="text-left px-2 py-1 font-normal">TYPE</th>
                <th className="text-left px-2 py-1 font-normal">BUY</th>
                <th className="text-left px-2 py-1 font-normal">SELL</th>
                <th className="text-right px-2 py-1 font-normal">BUY $</th>
                <th className="text-right px-2 py-1 font-normal">SELL $</th>
                <th className="text-right px-2 py-1 font-normal">EDGE</th>
                <th className="text-left px-2 py-1 font-normal">DESCRIPTION</th>
              </tr>
            </thead>
            <tbody>
              {data.map((arb, i) => (
                <tr
                  key={i}
                  className="border-b border-[#1e1e2e]/50 hover:bg-[#1a1a2a] transition-colors"
                >
                  <td className="px-2 py-1 font-mono text-amber-400 whitespace-nowrap">
                    {arb.type}
                  </td>
                  <td className="px-2 py-1 font-mono text-green-400 whitespace-nowrap">
                    {arb.buy_ticker.length > 18 ? arb.buy_ticker.slice(0, 18) + "\u2026" : arb.buy_ticker}
                  </td>
                  <td className="px-2 py-1 font-mono text-red-400 whitespace-nowrap">
                    {arb.sell_ticker.length > 18 ? arb.sell_ticker.slice(0, 18) + "\u2026" : arb.sell_ticker}
                  </td>
                  <td className="px-2 py-1 text-right font-mono text-[#e0e0e0]">
                    {(arb.buy_price * 100).toFixed(0)}c
                  </td>
                  <td className="px-2 py-1 text-right font-mono text-[#e0e0e0]">
                    {(arb.sell_price * 100).toFixed(0)}c
                  </td>
                  <td className={`px-2 py-1 text-right font-mono font-bold ${edgeColor(arb.edge)}`}>
                    {arb.edge >= 0 ? "+" : ""}
                    {(arb.edge * 100).toFixed(1)}c
                  </td>
                  <td className="px-2 py-1 text-[#888899] max-w-[250px] truncate">
                    {arb.description}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
