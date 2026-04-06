"use client";

import { useDashboard } from "@/lib/store";
import { fmtPrice, fmtDollar } from "@/lib/format";
import PanelHeader from "./PanelHeader";
import { useMemo, useState } from "react";

function holdTimeStr(minutes: number): string {
  if (minutes < 1) return "<1m";
  if (minutes < 60) return `${Math.floor(minutes)}m`;
  const h = Math.floor(minutes / 60);
  const m = Math.floor(minutes % 60);
  return m > 0 ? `${h}h${m}m` : `${h}h`;
}

function pnlColor(pnl: number, nearStop = false): string {
  if (nearStop) return "text-amber";
  return pnl >= 0 ? "text-green" : "text-red";
}

export default function TradeBlotter() {
  const {
    openPositions,
    closedPositions,
    positionSummary,
    executionStatus,
    toggleExecution,
    closePosition,
    markets,
  } = useDashboard();
  const [tab, setTab] = useState<"open" | "closed">("open");

  // Enrich open positions with live price from markets
  const enrichedOpen = useMemo(() => {
    return openPositions.map((p) => {
      const market = markets.find((m) => m.ticker === p.ticker);
      const livePrice = market?.price ?? p.current_price;
      // Recompute unrealized P&L with latest price
      let unrealized: number;
      if (p.direction === "BUY_YES") {
        unrealized = (livePrice - p.entry_price) * p.remaining_contracts - p.fees_paid;
      } else {
        unrealized = (p.entry_price - livePrice) * p.remaining_contracts - p.fees_paid;
      }
      // Is near stop-loss? (within 5% of entry)
      const lossPct = p.entry_cost > 0 ? -unrealized / p.entry_cost : 0;
      const nearStop = lossPct > 0.10;

      return { ...p, livePrice, unrealized, nearStop };
    });
  }, [openPositions, markets]);

  const totalUnrealized = enrichedOpen.reduce((s, p) => s + p.unrealized, 0);
  const totalRealized = positionSummary?.total_realized ?? 0;
  const totalPnL = totalUnrealized + totalRealized;
  const heat = positionSummary?.portfolio_heat ?? 0;

  return (
    <div className="flex flex-col h-full">
      <PanelHeader
        title="Execution"
        subtitle={
          executionStatus
            ? `${executionStatus.paused ? "PAUSED" : "ACTIVE"} | ${enrichedOpen.length} pos | ${(heat * 100).toFixed(0)}% heat`
            : `${enrichedOpen.length} positions`
        }
        right={
          <div className="flex items-center gap-2">
            <span className={`font-mono text-[11px] font-bold ${totalPnL >= 0 ? "text-green" : "text-red"}`}>
              {totalPnL >= 0 ? "+" : ""}${totalPnL.toFixed(2)}
            </span>
            {executionStatus && (
              <button
                onClick={toggleExecution}
                className={`px-1.5 py-0.5 rounded text-[9px] font-mono font-bold border transition-colors ${
                  executionStatus.paused
                    ? "bg-green/10 text-green border-green/30 hover:bg-green/20"
                    : "bg-red/10 text-red border-red/30 hover:bg-red/20"
                }`}
              >
                {executionStatus.paused ? "RESUME" : "PAUSE"}
              </button>
            )}
          </div>
        }
      />

      {/* Tab bar */}
      <div className="flex border-b border-border text-[10px]">
        <button
          onClick={() => setTab("open")}
          className={`flex-1 py-1.5 font-mono font-semibold transition-colors ${
            tab === "open" ? "text-blue border-b-2 border-blue" : "text-text-secondary hover:text-text-primary"
          }`}
        >
          Open ({enrichedOpen.length})
        </button>
        <button
          onClick={() => setTab("closed")}
          className={`flex-1 py-1.5 font-mono font-semibold transition-colors ${
            tab === "closed" ? "text-blue border-b-2 border-blue" : "text-text-secondary hover:text-text-primary"
          }`}
        >
          Closed ({closedPositions.length})
        </button>
      </div>

      <div className="flex-1 overflow-y-auto min-h-0">
        {tab === "open" ? (
          enrichedOpen.length === 0 ? (
            <div className="flex items-center justify-center h-full text-text-secondary text-[11px]">
              {executionStatus?.paused
                ? "Engine paused — no new positions"
                : "Waiting for signals with sufficient persistence..."}
            </div>
          ) : (
            <table className="w-full text-[10px]">
              <thead className="sticky top-0 bg-surface z-10">
                <tr className="text-text-secondary border-b border-border">
                  <th className="text-left px-2 py-1">Ticker</th>
                  <th className="text-center px-1 py-1">Dir</th>
                  <th className="text-right px-1 py-1">Qty</th>
                  <th className="text-right px-1 py-1">Entry</th>
                  <th className="text-right px-1 py-1">Live</th>
                  <th className="text-right px-1 py-1">P&L</th>
                  <th className="text-right px-1 py-1">Hold</th>
                  <th className="text-center px-1 py-1"></th>
                </tr>
              </thead>
              <tbody>
                {enrichedOpen.map((p, idx) => (
                  <tr
                    key={p.ticker}
                    className={`border-b border-border/30 hover:bg-border/30 ${
                      p.nearStop ? "bg-red/5" : idx % 2 === 1 ? "bg-bg/30" : ""
                    }`}
                  >
                    <td className="px-2 py-1.5" title={p.title || p.ticker}>
                      <div className="font-mono truncate max-w-[100px]">
                        {p.ticker.length > 18 ? p.ticker.slice(0, 18) + "\u2026" : p.ticker}
                      </div>
                      <div className="text-[8px] text-text-secondary">{p.regime_at_entry}</div>
                    </td>
                    <td
                      className={`text-center px-1 py-1.5 font-bold ${
                        p.direction === "BUY_YES" ? "text-green" : "text-red"
                      }`}
                    >
                      {p.direction === "BUY_YES" ? "YES" : "NO"}
                    </td>
                    <td className="text-right px-1 py-1.5 font-mono">
                      {p.remaining_contracts}
                      {p.status === "PARTIAL" && (
                        <span className="text-amber text-[8px] ml-0.5">/{p.contracts}</span>
                      )}
                    </td>
                    <td className="text-right px-1 py-1.5 font-mono text-text-secondary">
                      {fmtPrice(p.entry_price)}
                    </td>
                    <td className="text-right px-1 py-1.5 font-mono font-semibold">
                      {fmtPrice(p.livePrice)}
                    </td>
                    <td className="text-right px-1 py-1.5">
                      <span className={`font-mono font-semibold ${pnlColor(p.unrealized, p.nearStop)}`}>
                        {p.unrealized >= 0 ? "+" : ""}${p.unrealized.toFixed(2)}
                      </span>
                      <div className={`text-[8px] font-mono ${pnlColor(p.unrealized)}`}>
                        {((p.entry_cost > 0 ? p.unrealized / p.entry_cost : 0) * 100).toFixed(1)}%
                      </div>
                    </td>
                    <td className="text-right px-1 py-1.5 font-mono text-text-secondary text-[9px]">
                      {holdTimeStr(p.hold_time_minutes)}
                    </td>
                    <td className="text-center px-1 py-1.5">
                      <button
                        onClick={() => closePosition(p.ticker)}
                        className="px-1 py-0.5 rounded text-[8px] font-mono font-bold text-red/70 hover:text-red hover:bg-red/10 border border-red/20 transition-colors"
                        title="Manual close"
                      >
                        CLOSE
                      </button>
                    </td>
                  </tr>
                ))}
                {/* Summary row */}
                <tr className="border-t border-border bg-bg/50 font-semibold">
                  <td className="px-2 py-1.5 text-text-secondary" colSpan={2}>
                    <div className="text-[9px]">
                      Heat: <span className={`font-mono ${heat > 0.35 ? "text-amber" : "text-text-primary"}`}>{(heat * 100).toFixed(0)}%</span>
                    </div>
                  </td>
                  <td className="text-right px-1 py-1.5 font-mono text-text-secondary text-[9px]" colSpan={2}>
                    {fmtDollar(positionSummary?.total_deployed ?? 0)} deployed
                  </td>
                  <td className="text-right px-1 py-1.5 text-[9px] text-text-secondary">Unreal:</td>
                  <td className="text-right px-1 py-1.5">
                    <span className={`font-mono ${totalUnrealized >= 0 ? "text-green" : "text-red"}`}>
                      {totalUnrealized >= 0 ? "+" : ""}${totalUnrealized.toFixed(2)}
                    </span>
                  </td>
                  <td colSpan={2} />
                </tr>
              </tbody>
            </table>
          )
        ) : (
          /* Closed positions tab */
          closedPositions.length === 0 ? (
            <div className="flex items-center justify-center h-full text-text-secondary text-[11px]">
              No closed positions yet
            </div>
          ) : (
            <table className="w-full text-[10px]">
              <thead className="sticky top-0 bg-surface z-10">
                <tr className="text-text-secondary border-b border-border">
                  <th className="text-left px-2 py-1">Ticker</th>
                  <th className="text-center px-1 py-1">Dir</th>
                  <th className="text-right px-1 py-1">Qty</th>
                  <th className="text-right px-1 py-1">Entry</th>
                  <th className="text-right px-1 py-1">Exit</th>
                  <th className="text-right px-1 py-1">P&L</th>
                  <th className="text-left px-1 py-1">Reason</th>
                </tr>
              </thead>
              <tbody>
                {closedPositions.slice(0, 50).map((p, idx) => {
                  const net = p.realized_pnl;
                  return (
                    <tr
                      key={`${p.ticker}-${p.entry_time}`}
                      className={`border-b border-border/30 ${
                        net > 0 ? "bg-green/5" : net < 0 ? "bg-red/5" : idx % 2 === 1 ? "bg-bg/30" : ""
                      }`}
                    >
                      <td className="px-2 py-1 font-mono truncate max-w-[100px]" title={p.ticker}>
                        {p.ticker.length > 18 ? p.ticker.slice(0, 18) + "\u2026" : p.ticker}
                      </td>
                      <td className={`text-center px-1 py-1 font-bold ${p.direction === "BUY_YES" ? "text-green" : "text-red"}`}>
                        {p.direction === "BUY_YES" ? "YES" : "NO"}
                      </td>
                      <td className="text-right px-1 py-1 font-mono">{p.contracts}</td>
                      <td className="text-right px-1 py-1 font-mono">{fmtPrice(p.entry_price)}</td>
                      <td className="text-right px-1 py-1 font-mono">{fmtPrice(p.exit_price)}</td>
                      <td className={`text-right px-1 py-1 font-mono font-semibold ${net >= 0 ? "text-green" : "text-red"}`}>
                        {net >= 0 ? "+" : ""}${net.toFixed(2)}
                      </td>
                      <td className="px-1 py-1 text-text-secondary text-[9px] truncate max-w-[80px]" title={p.exit_reason}>
                        {p.exit_reason}
                      </td>
                    </tr>
                  );
                })}
                {/* Summary */}
                <tr className="border-t border-border bg-bg/50 font-semibold">
                  <td className="px-2 py-1.5 text-text-secondary" colSpan={4}>
                    {closedPositions.length} closed
                  </td>
                  <td className="text-right px-1 py-1.5 text-[9px] text-text-secondary">Real:</td>
                  <td className={`text-right px-1 py-1.5 font-mono ${totalRealized >= 0 ? "text-green" : "text-red"}`}>
                    {totalRealized >= 0 ? "+" : ""}${totalRealized.toFixed(2)}
                  </td>
                  <td />
                </tr>
              </tbody>
            </table>
          )
        )}
      </div>
    </div>
  );
}
