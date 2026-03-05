"use client";

import { useDashboard } from "@/lib/store";
import { fmtPrice, fmtDollar } from "@/lib/format";
import PanelHeader from "./PanelHeader";
import { useMemo } from "react";

export default function TradeBlotter() {
  const { signals, markets } = useDashboard();

  const positions = useMemo(() => {
    return signals
      .filter((s) => s.recommended_contracts > 0)
      .map((s) => {
        const market = markets.find((m) => m.ticker === s.ticker);
        const livePrice = market?.price ?? s.current_price;
        const entryPrice = s.current_price;

        const priceDelta = livePrice - entryPrice;
        const pnl =
          s.direction === "BUY_YES"
            ? priceDelta * s.recommended_contracts
            : -priceDelta * s.recommended_contracts;

        let action: "HOLD" | "TAKE_PROFIT" | "STOP_LOSS" = "HOLD";
        if (s.direction === "BUY_YES") {
          if (livePrice >= s.risk.take_profit) action = "TAKE_PROFIT";
          else if (livePrice <= s.risk.stop_loss) action = "STOP_LOSS";
        } else {
          if (livePrice <= 1 - s.risk.take_profit) action = "TAKE_PROFIT";
          else if (livePrice >= 1 - s.risk.stop_loss) action = "STOP_LOSS";
        }

        return { ...s, livePrice, pnl, action, positionValue: s.risk.size_dollars };
      })
      .sort((a, b) => Math.abs(b.edge) - Math.abs(a.edge));
  }, [signals, markets]);

  const totalPnL = positions.reduce((sum, p) => sum + p.pnl, 0);
  const totalValue = positions.reduce((sum, p) => sum + p.positionValue, 0);

  return (
    <div className="flex flex-col h-full">
      <PanelHeader
        title="Trade Blotter"
        subtitle={`${positions.length} positions`}
        right={
          <span className={`font-mono text-[11px] font-bold ${totalPnL >= 0 ? "text-green" : "text-red"}`}>
            P&L {totalPnL >= 0 ? "+" : ""}${totalPnL.toFixed(2)}
          </span>
        }
      />
      <div className="flex-1 overflow-y-auto min-h-0">
        {positions.length === 0 ? (
          <div className="flex items-center justify-center h-full text-text-secondary text-sm">
            No active positions
          </div>
        ) : (
          <table className="w-full text-[10px]">
            <thead className="sticky top-0 bg-surface z-10">
              <tr className="text-text-secondary border-b border-border">
                <th className="text-left px-2 py-1">Ticker</th>
                <th className="text-center px-2 py-1">Dir</th>
                <th className="text-right px-2 py-1">Qty</th>
                <th className="text-right px-2 py-1">Entry</th>
                <th className="text-right px-2 py-1">Live</th>
                <th className="text-right px-2 py-1">P&L</th>
                <th className="text-center px-2 py-1">Action</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p, idx) => (
                <tr
                  key={p.ticker}
                  className={`border-b border-border/30 hover:bg-border/30 ${idx % 2 === 1 ? "bg-bg/30" : ""}`}
                >
                  <td className="px-2 py-1.5" title={p.title || p.ticker}>
                    <div className="font-mono truncate max-w-[110px]">
                      {p.ticker.length > 20 ? p.ticker.slice(0, 20) + "\u2026" : p.ticker}
                    </div>
                  </td>
                  <td
                    className={`text-center px-2 py-1.5 font-bold ${
                      p.direction === "BUY_YES" ? "text-green" : "text-red"
                    }`}
                  >
                    {p.direction === "BUY_YES" ? "YES" : "NO"}
                  </td>
                  <td className="text-right px-2 py-1.5 font-mono">{p.recommended_contracts}</td>
                  <td className="text-right px-2 py-1.5 font-mono text-text-secondary">
                    {fmtPrice(p.current_price)}
                  </td>
                  <td className="text-right px-2 py-1.5 font-mono font-semibold">
                    {fmtPrice(p.livePrice)}
                  </td>
                  <td className="text-right px-2 py-1.5">
                    <span className={`font-mono font-semibold ${p.pnl >= 0 ? "text-green" : "text-red"}`}>
                      {p.pnl >= 0 ? "+" : ""}${p.pnl.toFixed(2)}
                    </span>
                  </td>
                  <td className="text-center px-2 py-1.5">
                    <span
                      className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${
                        p.action === "TAKE_PROFIT"
                          ? "bg-green/20 text-green"
                          : p.action === "STOP_LOSS"
                          ? "bg-red/20 text-red"
                          : "bg-blue/10 text-blue"
                      }`}
                    >
                      {p.action.replace("_", " ")}
                    </span>
                  </td>
                </tr>
              ))}
              {/* Summary row */}
              <tr className="border-t border-border bg-bg/50 font-semibold">
                <td className="px-2 py-1.5 text-text-secondary" colSpan={2}>
                  Total
                </td>
                <td className="text-right px-2 py-1.5 font-mono text-text-secondary" colSpan={2}>
                  {fmtDollar(totalValue)} deployed
                </td>
                <td className="text-right px-2 py-1.5" />
                <td className="text-right px-2 py-1.5">
                  <span className={`font-mono ${totalPnL >= 0 ? "text-green" : "text-red"}`}>
                    {totalPnL >= 0 ? "+" : ""}${totalPnL.toFixed(2)}
                  </span>
                </td>
                <td />
              </tr>
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
