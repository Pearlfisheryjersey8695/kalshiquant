"use client";

import { useDashboard } from "@/lib/store";
import { fmtPrice, fmtDollar } from "@/lib/format";
import PanelHeader from "./PanelHeader";
import { useMemo } from "react";

const DONUT_COLORS = ["#3b82f6", "#00d26a", "#f59e0b", "#ff3b3b", "#a855f7", "#06b6d4"];

export default function PortfolioPanel() {
  const { openPositions, positionSummary, signals, markets } = useDashboard();

  const bankroll = positionSummary?.bankroll ?? 10000;
  const totalDeployed = positionSummary?.total_deployed ?? 0;
  const totalUnrealized = positionSummary?.total_unrealized ?? 0;
  const totalRealized = positionSummary?.total_realized ?? 0;
  const heat = positionSummary?.portfolio_heat ?? 0;
  const cash = bankroll - totalDeployed;
  const totalPnl = totalUnrealized + totalRealized;

  // Category allocation from open positions
  const catAlloc = useMemo(() => {
    const map = new Map<string, number>();
    openPositions.forEach((p) => {
      const cat = p.category || "Other";
      const deployed = p.entry_cost * (p.remaining_contracts / (p.contracts || 1));
      map.set(cat, (map.get(cat) || 0) + deployed);
    });
    map.set("Cash", cash);
    return Array.from(map.entries()).sort((a, b) => b[1] - a[1]);
  }, [openPositions, cash]);

  // Enrich open positions with live prices
  const enrichedPositions = useMemo(() => {
    return openPositions.map((p) => {
      const market = markets.find((m) => m.ticker === p.ticker);
      const livePrice = market?.price ?? p.current_price;
      let unrealized: number;
      if (p.direction === "BUY_YES") {
        unrealized = (livePrice - p.entry_price) * p.remaining_contracts - p.fees_paid;
      } else {
        unrealized = (p.entry_price - livePrice) * p.remaining_contracts - p.fees_paid;
      }
      return { ...p, livePrice, unrealized };
    });
  }, [openPositions, markets]);

  // Actionable signals (not already in an open position)
  const tradedTickers = new Set(openPositions.map((p) => p.ticker));
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
            <div className="font-mono text-sm font-bold text-green">{fmtDollar(cash)}</div>
          </div>
          <div className="bg-bg rounded p-2 border border-border">
            <div className="text-[9px] text-text-secondary uppercase">Deployed</div>
            <div className="font-mono text-sm font-bold text-blue">{fmtDollar(totalDeployed)}</div>
          </div>
          <div className="bg-bg rounded p-2 border border-border">
            <div className="text-[9px] text-text-secondary uppercase">Heat</div>
            <div className={`font-mono text-sm font-bold ${heat > 0.35 ? "text-amber" : "text-text-primary"}`}>
              {(heat * 100).toFixed(0)}%
            </div>
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

        {/* Pending signals (auto-execution will pick these up) */}
        {actionableSignals.length > 0 && (
          <div>
            <div className="text-[10px] text-text-secondary uppercase tracking-wider mb-1.5">
              Pending Signals ({actionableSignals.length})
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
                  <span className={`px-2 py-1 rounded text-[9px] font-bold ${
                    sig.direction === "BUY_YES"
                      ? "bg-green/10 text-green"
                      : "bg-red/10 text-red"
                  }`}>
                    {sig.direction === "BUY_YES" ? "YES" : "NO"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Open positions summary */}
        {enrichedPositions.length > 0 && (
          <div>
            <div className="text-[10px] text-text-secondary uppercase tracking-wider mb-1.5">
              Open Positions ({enrichedPositions.length})
            </div>
            <div className="space-y-1">
              {enrichedPositions.map((p) => (
                <div key={p.ticker} className="flex items-center justify-between bg-bg/50 rounded px-2 py-1 text-[10px]">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className={`font-bold ${p.direction === "BUY_YES" ? "text-green" : "text-red"}`}>
                      {p.direction === "BUY_YES" ? "YES" : "NO"}
                    </span>
                    <span className="font-mono truncate max-w-[100px]" title={p.ticker}>
                      {p.ticker.length > 16 ? p.ticker.slice(0, 16) + "\u2026" : p.ticker}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    <span className="font-mono text-text-secondary">{p.remaining_contracts}x</span>
                    <span className={`font-mono font-semibold ${p.unrealized >= 0 ? "text-green" : "text-red"}`}>
                      {p.unrealized >= 0 ? "+" : ""}${p.unrealized.toFixed(2)}
                    </span>
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
