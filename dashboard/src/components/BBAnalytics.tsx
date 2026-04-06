"use client";

import { api } from "@/lib/api";
import { useState, useEffect } from "react";
import BBBacktest from "./BBBacktest";
import type { CorrelationData } from "@/lib/types";

// ── Helpers ─────────────────────────────────────────────────────────────────

function corrColor(c: number): string {
  const abs = Math.abs(c);
  if (abs > 0.8) return "text-red-400";
  if (abs > 0.6) return "text-amber-400";
  if (abs > 0.4) return "text-[#e0e0e0]";
  return "text-[#888899]";
}

// ── Main Component ──────────────────────────────────────────────────────────

export default function BBAnalytics() {
  const [correlations, setCorrelations] = useState<CorrelationData | null>(null);

  useEffect(() => {
    api.getCorrelations().then(setCorrelations).catch(console.error);
    const id = setInterval(() => {
      api.getCorrelations().then(setCorrelations).catch(() => {});
    }, 30000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="h-full flex flex-col bg-[#0a0a0f] font-mono text-[13px] overflow-hidden">
      {/* Header */}
      <div className="h-[32px] bg-[#12121a] border-b border-[#1e1e2e] flex items-center px-4 shrink-0">
        <span className="text-bb-orange text-[13px] font-bold tracking-wider">F3 ANALYTICS</span>
        <span className="text-[#888899] text-[11px] ml-3">Deep Quantitative Analysis</span>
      </div>

      {/* Scrollable content */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {/* Backtest section -- full BBBacktest */}
        <div className="min-h-[600px]">
          <BBBacktest />
        </div>

        {/* Cross-Market Correlations */}
        <div className="border-t border-[#1e1e2e]">
          <div className="h-[32px] bg-[#12121a] border-b border-[#1e1e2e] flex items-center px-4">
            <span className="text-bb-orange text-[11px] font-bold tracking-wider">CROSS-MARKET CORRELATIONS</span>
            {correlations && (
              <span className="text-[#888899] text-[10px] ml-3">
                {correlations.matrix.length} pairs | {correlations.divergences.length} divergences
              </span>
            )}
          </div>

          {!correlations ? (
            <div className="px-4 py-8 text-[11px] text-[#888899] text-center">LOADING CORRELATION DATA...</div>
          ) : (
            <div className="p-3 space-y-3">
              {/* Divergence Alerts */}
              {correlations.divergences.length > 0 && (
                <div className="bg-[#12121a] border border-[#1e1e2e] rounded">
                  <div className="px-3 py-1.5 border-b border-[#1e1e2e]">
                    <span className="text-red-400 text-[10px] font-bold tracking-wider">DIVERGENCE ALERTS</span>
                    <span className="text-[10px] text-[#888899] ml-2">{correlations.divergences.length}</span>
                  </div>
                  <div className="p-2 space-y-1">
                    {correlations.divergences.map((d, i) => (
                      <div
                        key={i}
                        className="flex items-center gap-2 text-[11px] py-1 px-2 bg-red-500/10 border border-red-500/30 rounded"
                      >
                        <span className="text-red-400 font-bold text-[10px] shrink-0">DIVERGE</span>
                        <span className="text-[#e0e0e0] truncate">
                          {d.t1.length > 14 ? d.t1.slice(0, 14) : d.t1}
                        </span>
                        <span className="text-[#888899]">/</span>
                        <span className="text-[#e0e0e0] truncate">
                          {d.t2.length > 14 ? d.t2.slice(0, 14) : d.t2}
                        </span>
                        <span className="text-amber-400 ml-auto shrink-0">
                          r={d.correlation.toFixed(2)}
                        </span>
                        <span className="text-red-400 shrink-0">
                          spr={d.spread.toFixed(2)}
                        </span>
                        <span className="text-[#888899] shrink-0">
                          {d.signal}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Top Correlated Pairs Table */}
              <div className="bg-[#12121a] border border-[#1e1e2e] rounded">
                <div className="px-3 py-1.5 border-b border-[#1e1e2e]">
                  <span className="text-bb-orange text-[10px] font-bold tracking-wider">TOP CORRELATED PAIRS</span>
                </div>
                <CorrelationTable data={correlations} />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Correlation Table ───────────────────────────────────────────────────────

function CorrelationTable({ data }: { data: CorrelationData }) {
  const topPairs = [...data.matrix]
    .sort((a, b) => Math.abs(b.corr) - Math.abs(a.corr))
    .slice(0, 20);

  if (topPairs.length === 0) {
    return <div className="p-4 text-[11px] text-[#888899] text-center">NO CORRELATION DATA</div>;
  }

  return (
    <div className="max-h-[300px] overflow-y-auto">
      <table className="w-full text-[11px]">
        <thead className="sticky top-0 bg-[#12121a]">
          <tr className="text-[#888899] border-b border-[#1e1e2e]">
            <th className="text-left px-3 py-1 font-normal">TICKER 1</th>
            <th className="text-left px-3 py-1 font-normal">TICKER 2</th>
            <th className="text-right px-3 py-1 font-normal">CORRELATION</th>
            <th className="text-center px-3 py-1 font-normal">STRENGTH</th>
          </tr>
        </thead>
        <tbody>
          {topPairs.map((p, i) => {
            const abs = Math.abs(p.corr);
            const strengthLabel = abs > 0.8 ? "STRONG" : abs > 0.6 ? "MODERATE" : abs > 0.4 ? "WEAK" : "NOISE";
            const strengthColor = abs > 0.8 ? "text-red-400 bg-red-500/10" : abs > 0.6 ? "text-amber-400 bg-amber-500/10" : abs > 0.4 ? "text-[#e0e0e0] bg-[#1e1e2e]" : "text-[#888899] bg-[#1e1e2e]";
            return (
              <tr key={i} className="border-b border-[#1e1e2e]/50 hover:bg-[#1a1a2a] transition-colors">
                <td className="px-3 py-1 text-[#e0e0e0] truncate max-w-[160px]">
                  {p.t1.length > 18 ? p.t1.slice(0, 18) + "\u2026" : p.t1}
                </td>
                <td className="px-3 py-1 text-[#e0e0e0] truncate max-w-[160px]">
                  {p.t2.length > 18 ? p.t2.slice(0, 18) + "\u2026" : p.t2}
                </td>
                <td className={`px-3 py-1 text-right font-bold ${corrColor(p.corr)}`}>
                  {p.corr >= 0 ? "+" : ""}{p.corr.toFixed(3)}
                </td>
                <td className="px-3 py-1 text-center">
                  <span className={`px-1.5 py-0.5 text-[9px] rounded ${strengthColor}`}>
                    {strengthLabel}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
