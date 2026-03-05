"use client";

import { useDashboard } from "@/lib/store";
import { fmtPrice, fmtDollar, fmtRelativeTime } from "@/lib/format";
import PanelHeader from "./PanelHeader";
import { useMemo } from "react";

const DONUT_COLORS = ["#3b82f6", "#00d26a", "#f59e0b", "#ff3b3b", "#a855f7", "#06b6d4"];

export default function PortfolioPanel() {
  const { simCash, simTrades, signals, markets, executeTrade } = useDashboard();

  const totalDeployed = simTrades.reduce((sum, t) => sum + t.sizeDollars, 0);
  const totalValue = simCash + totalDeployed;

  // Category allocation from executed trades
  const catAlloc = useMemo(() => {
    const map = new Map<string, number>();
    simTrades.forEach((t) => {
      const sig = signals.find((s) => s.ticker === t.ticker);
      const cat = sig?.category || "Other";
      map.set(cat, (map.get(cat) || 0) + t.sizeDollars);
    });
    // Add cash
    map.set("Cash", simCash);
    return Array.from(map.entries()).sort((a, b) => b[1] - a[1]);
  }, [simTrades, signals, simCash]);

  // Live P&L for sim trades
  const tradesWithPnl = useMemo(() => {
    return simTrades.map((t) => {
      const mkt = markets.find((m) => m.ticker === t.ticker);
      const livePrice = mkt?.price ?? t.entryPrice;
      const priceDelta = livePrice - t.entryPrice;
      const pnl = t.direction === "BUY_YES" ? priceDelta * t.contracts : -priceDelta * t.contracts;
      return { ...t, livePrice, pnl };
    });
  }, [simTrades, markets]);

  const totalPnl = tradesWithPnl.reduce((sum, t) => sum + t.pnl, 0);

  // Actionable signals (not already traded)
  const tradedTickers = new Set(simTrades.map((t) => t.ticker));
  const actionableSignals = signals.filter(
    (s) => s.recommended_contracts > 0 && !tradedTickers.has(s.ticker)
  );

  return (
    <div className="flex flex-col h-full">
      <PanelHeader
        title="Portfolio"
        right={
          <span className={`font-mono text-[11px] font-bold ${totalPnl >= 0 ? "text-green" : "text-red"}`}>
            P&L {totalPnl >= 0 ? "+" : ""}${totalPnl.toFixed(2)}
          </span>
        }
      />
      <div className="flex-1 overflow-y-auto p-3 space-y-3 min-h-0">
        {/* Portfolio summary */}
        <div className="grid grid-cols-3 gap-2">
          <div className="bg-bg rounded p-2 border border-border">
            <div className="text-[9px] text-text-secondary uppercase">Cash</div>
            <div className="font-mono text-sm font-bold text-green">{fmtDollar(simCash)}</div>
          </div>
          <div className="bg-bg rounded p-2 border border-border">
            <div className="text-[9px] text-text-secondary uppercase">Deployed</div>
            <div className="font-mono text-sm font-bold text-blue">{fmtDollar(totalDeployed)}</div>
          </div>
          <div className="bg-bg rounded p-2 border border-border">
            <div className="text-[9px] text-text-secondary uppercase">Total</div>
            <div className="font-mono text-sm font-bold">{fmtDollar(totalValue)}</div>
          </div>
        </div>

        {/* Allocation donut */}
        {catAlloc.length > 0 && (
          <div className="flex items-start gap-3">
            <svg viewBox="0 0 100 100" className="w-14 h-14 shrink-0 -rotate-90">
              {(() => {
                const total = catAlloc.reduce((s, [, v]) => s + v, 0);
                let offset = 0;
                return catAlloc.map(([cat, val], i) => {
                  const pct = total > 0 ? (val / total) * 251 : 0;
                  const el = (
                    <circle
                      key={cat}
                      cx="50" cy="50" r="40"
                      fill="none"
                      stroke={cat === "Cash" ? "#888899" : DONUT_COLORS[i % DONUT_COLORS.length]}
                      strokeWidth="10"
                      strokeDasharray={`${pct} ${251 - pct}`}
                      strokeDashoffset={-offset}
                    />
                  );
                  offset += pct;
                  return el;
                });
              })()}
            </svg>
            <div className="flex-1 space-y-0.5">
              {catAlloc.map(([cat, val], i) => (
                <div key={cat} className="flex items-center justify-between text-[10px]">
                  <div className="flex items-center gap-1.5">
                    <div
                      className="w-2 h-2 rounded-sm"
                      style={{ background: cat === "Cash" ? "#888899" : DONUT_COLORS[i % DONUT_COLORS.length] }}
                    />
                    <span className="text-text-secondary">{cat}</span>
                  </div>
                  <span className="font-mono">{fmtDollar(val)}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Actionable signals with Buy button */}
        {actionableSignals.length > 0 && (
          <div>
            <div className="text-[10px] text-text-secondary uppercase tracking-wider mb-1.5">
              Available Signals
            </div>
            <div className="space-y-1">
              {actionableSignals.slice(0, 5).map((sig) => (
                <div
                  key={sig.ticker}
                  className="flex items-center gap-2 bg-bg rounded p-2 border border-border"
                >
                  <div className="flex-1 min-w-0">
                    <div className="font-mono text-[10px] truncate" title={sig.title}>
                      {sig.ticker.length > 22 ? sig.ticker.slice(0, 22) + "\u2026" : sig.ticker}
                    </div>
                    <div className="text-[9px] text-text-secondary">
                      {sig.recommended_contracts} contracts @ {fmtPrice(sig.current_price)}
                      <span className={`ml-1 font-semibold ${sig.edge > 0 ? "text-green" : "text-red"}`}>
                        {sig.edge > 0 ? "+" : ""}{(sig.edge * 100).toFixed(1)}c
                      </span>
                    </div>
                  </div>
                  <button
                    onClick={() => executeTrade(sig)}
                    disabled={simCash < sig.risk.size_dollars}
                    className={`px-2 py-1 rounded text-[9px] font-bold transition-colors ${
                      sig.direction === "BUY_YES"
                        ? "bg-green/20 text-green hover:bg-green/30 disabled:opacity-30"
                        : "bg-red/20 text-red hover:bg-red/30 disabled:opacity-30"
                    }`}
                  >
                    {sig.direction === "BUY_YES" ? "BUY YES" : "BUY NO"} ({fmtDollar(sig.risk.size_dollars)})
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Transaction history */}
        {tradesWithPnl.length > 0 && (
          <div>
            <div className="text-[10px] text-text-secondary uppercase tracking-wider mb-1.5">
              Trade History ({tradesWithPnl.length})
            </div>
            <div className="space-y-1">
              {tradesWithPnl.map((t) => (
                <div key={t.id} className="flex items-center justify-between bg-bg/50 rounded px-2 py-1 text-[10px]">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className={`font-bold ${t.direction === "BUY_YES" ? "text-green" : "text-red"}`}>
                      {t.direction === "BUY_YES" ? "YES" : "NO"}
                    </span>
                    <span className="font-mono truncate max-w-[100px]" title={t.ticker}>
                      {t.ticker.length > 16 ? t.ticker.slice(0, 16) + "\u2026" : t.ticker}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    <span className={`font-mono font-semibold ${t.pnl >= 0 ? "text-green" : "text-red"}`}>
                      {t.pnl >= 0 ? "+" : ""}${t.pnl.toFixed(2)}
                    </span>
                    <span className="text-text-secondary">{fmtRelativeTime(t.ts)}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
