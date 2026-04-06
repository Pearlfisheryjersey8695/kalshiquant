"use client";

import { useDashboard } from "@/lib/store";
import { fmtPrice, fmtRelativeTime } from "@/lib/format";

export default function BBBottomBar() {
  const { openPositions, closedPositions, positionSummary, executionStatus, feedEvents, toggleExecution } = useDashboard();

  const pnl = positionSummary?.today_pnl ?? 0;
  const realized = positionSummary?.total_realized ?? 0;
  const unrealized = positionSummary?.total_unrealized ?? 0;
  const heat = positionSummary?.portfolio_heat ?? 0;
  const openCount = positionSummary?.open_positions ?? 0;
  const deployed = positionSummary?.total_deployed ?? 0;

  // Merge open + recent closed for blotter
  const blotterRows = [
    ...openPositions.map((p) => ({
      time: p.entry_time,
      ticker: p.ticker,
      action: p.direction === "BUY_YES" ? "BUY" : "SELL",
      size: p.remaining_contracts,
      price: p.entry_price,
      pnl: p.unrealized_pnl,
      status: "OPEN" as const,
    })),
    ...closedPositions.slice(-10).reverse().map((p) => ({
      time: p.exit_time || p.entry_time,
      ticker: p.ticker,
      action: p.exit_reason || "CLOSE",
      size: p.contracts,
      price: p.exit_price || p.entry_price,
      pnl: p.realized_pnl,
      status: "CLOSED" as const,
    })),
  ].sort((a, b) => (b.time ?? "").localeCompare(a.time ?? ""));

  // Recent feed events for the ticker strip
  const recentEvents = feedEvents.slice(0, 20);

  return (
    <div className="flex h-full">
      {/* LEFT: Trade Blotter */}
      <div className="flex flex-col flex-[2] border-r border-bb-border min-w-0">
        <div className="bb-panel-title flex items-center justify-between">
          <span>TRADE BLOTTER</span>
          <button
            onClick={toggleExecution}
            className={`text-[9px] px-2 py-0 border ${
              executionStatus?.paused
                ? "border-bb-green text-bb-green hover:bg-bb-green/10"
                : "border-bb-yellow text-bb-yellow hover:bg-bb-yellow/10"
            }`}
          >
            {executionStatus?.paused ? "RESUME" : "PAUSE"}
          </button>
        </div>
        <div className="bb-panel-body">
          <table className="bb-table">
            <thead>
              <tr>
                <th>TIME</th>
                <th>TICKER</th>
                <th>ACTION</th>
                <th style={{ textAlign: "right" }}>SIZE</th>
                <th style={{ textAlign: "right" }}>PRICE</th>
                <th style={{ textAlign: "right" }}>PNL</th>
              </tr>
            </thead>
            <tbody>
              {blotterRows.length === 0 ? (
                <tr><td colSpan={6} className="text-bb-dim text-center py-2">NO TRADES</td></tr>
              ) : (
                blotterRows.map((row, i) => (
                  <tr key={i}>
                    <td className="text-bb-dim text-[9px]">{fmtRelativeTime(row.time)}</td>
                    <td className="truncate-ticker" style={{ maxWidth: 140 }}>{row.ticker.length > 20 ? row.ticker.slice(0, 20) + "\u2026" : row.ticker}</td>
                    <td className={row.action === "BUY" ? "bb-green" : row.action === "SELL" ? "bb-red" : "bb-yellow"}>
                      {row.action}
                    </td>
                    <td style={{ textAlign: "right" }}>{row.size}</td>
                    <td style={{ textAlign: "right" }}>{fmtPrice(row.price)}</td>
                    <td style={{ textAlign: "right" }} className={row.pnl >= 0 ? "bb-green" : "bb-red"}>
                      {row.pnl >= 0 ? "+" : ""}{row.pnl.toFixed(2)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* CENTER: Risk Metrics */}
      <div className="flex flex-col flex-[1.5] border-r border-bb-border min-w-0">
        <div className="bb-panel-title">RISK METRICS</div>
        <div className="p-2 flex flex-col gap-[3px] text-[10px]">
          <RiskRow label="TOTAL P&L" value={`${pnl >= 0 ? "+" : ""}$${pnl.toFixed(2)}`} color={pnl >= 0 ? "#00ff00" : "#ff0000"} />
          <RiskRow label="REALIZED" value={`${realized >= 0 ? "+" : ""}$${realized.toFixed(2)}`} color={realized >= 0 ? "#00ff00" : "#ff0000"} />
          <RiskRow label="UNREALIZED" value={`${unrealized >= 0 ? "+" : ""}$${unrealized.toFixed(2)}`} color={unrealized >= 0 ? "#00ff00" : "#ff0000"} />
          <div className="border-b border-bb-border my-[2px]" />
          <RiskRow label="OPEN POS" value={String(openCount)} />
          <RiskRow label="EXPOSURE" value={`$${deployed.toFixed(0)}`} />
          <RiskRow label="HEAT" value={`${(heat * 100).toFixed(1)}%`} color={heat > 0.35 ? "#ff0000" : heat > 0.2 ? "#ffff00" : "#ffffff"} />
          <div className="border-b border-bb-border my-[2px]" />
          <RiskRow label="ENGINE" value={executionStatus?.paused ? "PAUSED" : "ACTIVE"} color={executionStatus?.paused ? "#ffff00" : "#00ff00"} />
          <RiskRow label="BANKROLL" value={`$${(positionSummary?.bankroll ?? 10000).toFixed(0)}`} />
        </div>
      </div>

      {/* RIGHT: Live Event Ticker */}
      <div className="flex flex-col flex-[2] min-w-0">
        <div className="bb-panel-title flex items-center justify-between">
          <span>LIVE EVENTS</span>
          <span className="text-bb-dim">{feedEvents.length}</span>
        </div>
        <div className="bb-panel-body">
          {recentEvents.length === 0 ? (
            <div className="text-bb-dim text-[10px] p-2">WAITING FOR EVENTS...</div>
          ) : (
            recentEvents.map((ev) => (
              <div key={ev.seq} className="flex items-baseline gap-2 px-2 py-[1px] text-[10px] hover:bg-bb-row-even">
                <span className="text-bb-dim text-[9px] w-[40px] shrink-0 text-right">{fmtRelativeTime(ev.ts)}</span>
                <span className={
                  ev.event_type === "TRADE" ? "text-bb-green" :
                  ev.event_type === "SIGNAL_CHANGE" ? "text-bb-yellow" :
                  ev.event_type === "REGIME_CHANGE" ? "text-bb-blue" :
                  ev.event_type === "ERROR" ? "text-bb-red" :
                  "text-bb-dim"
                } style={{ width: 70, flexShrink: 0 }}>
                  {ev.event_type.replace(/_/g, " ")}
                </span>
                <span className="text-bb-white truncate">{ev.message}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

function RiskRow({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-bb-orange">{label}</span>
      <span style={color ? { color } : { color: "#ffffff" }}>{value}</span>
    </div>
  );
}
