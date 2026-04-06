"use client";

import { useDashboard } from "@/lib/store";
import { api } from "@/lib/api";

import { useState, useEffect } from "react";

// ── Helpers ─────────────────────────────────────────────────────────────────

function pnlColor(v: number) {
  return v > 0 ? "text-green-400" : v < 0 ? "text-red-400" : "text-[#888899]";
}

function pnlSign(v: number) {
  return v >= 0 ? `+$${v.toFixed(2)}` : `-$${Math.abs(v).toFixed(2)}`;
}

function fmtRelativeTime(ts: string) {
  if (!ts) return "";
  const diff = (Date.now() - new Date(ts).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
  return `${Math.floor(diff / 86400)}d`;
}

// ── Component ───────────────────────────────────────────────────────────────

export default function BBExecute() {
  const {
    openPositions,
    closedPositions,
    positionSummary,
    executionStatus,
    feedEvents,
    toggleExecution,
    closePosition,
  } = useDashboard();

  // AI Agent: brain status polled every 10s
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [brainStatus, setBrainStatus] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    const fetchBrain = () =>
      api.getBrainStatus().then(setBrainStatus).catch(() => {});
    fetchBrain();
    const id = setInterval(fetchBrain, 10000);
    return () => clearInterval(id);
  }, []);

  const pnl = positionSummary?.today_pnl ?? 0;
  const realized = positionSummary?.total_realized ?? 0;
  const unrealized = positionSummary?.total_unrealized ?? 0;
  const heat = positionSummary?.portfolio_heat ?? 0;
  const bankroll = positionSummary?.bankroll ?? 10000;
  const openCount = positionSummary?.open_positions ?? 0;
  const deployed = positionSummary?.total_deployed ?? 0;
  const engineActive = executionStatus ? !executionStatus.paused : false;

  // Blotter rows: open + recent closed
  const blotterRows = [
    ...openPositions.map((p) => ({
      time: p.entry_time,
      ticker: p.ticker,
      direction: p.direction,
      action: p.direction === "BUY_YES" ? "BUY" : "SELL",
      size: p.remaining_contracts,
      price: p.entry_price,
      pnl: p.unrealized_pnl,
      status: "OPEN" as const,
    })),
    ...closedPositions.slice(-10).reverse().map((p) => ({
      time: p.exit_time || p.entry_time,
      ticker: p.ticker,
      direction: p.direction,
      action: p.exit_reason || "CLOSE",
      size: p.contracts,
      price: p.exit_price || p.entry_price,
      pnl: p.realized_pnl,
      status: "CLOSED" as const,
    })),
  ].sort((a, b) => (b.time ?? "").localeCompare(a.time ?? ""));

  // Brain data
  const cycleCount = (brainStatus?.cycle_count as number) ?? 0;
  const rlStats = (brainStatus?.rl_stats as { q_states: number }) ?? { q_states: 0 };
  const pendingTheses = (brainStatus?.pending_theses ?? {}) as Record<string, Record<string, unknown>>;
  const recentDecisions = (brainStatus?.recent_decisions ?? []) as Array<{
    cycle: number;
    entries_executed: number;
    theses_generated: number;
    skipped: number;
    ts?: string;
    elapsed_ms?: number;
  }>;

  // Feed events
  const recentEvents = feedEvents.slice(0, 20);

  return (
    <div className="h-full flex flex-col overflow-hidden bg-bb-black font-mono">
      {/* ── Execution Status Strip ────────────────────────────────────── */}
      <div className="h-[34px] bg-[#12121a] border-b border-bb-border flex items-center justify-between px-4 shrink-0 text-[11px]">
        <div className="flex items-center gap-4">
          <span className="text-bb-orange font-bold tracking-wider">F4 EXECUTE</span>
          <span className={engineActive ? "text-green-400" : "text-red-400"}>
            ENGINE: {engineActive ? "ACTIVE" : "PAUSED"}
          </span>
          <span className="text-bb-dim">|</span>
          <span className={pnlColor(pnl)}>P&L: {pnlSign(pnl)}</span>
          <span className="text-bb-dim">|</span>
          <span className={pnlColor(realized)}>REALIZED: {pnlSign(realized)}</span>
          <span className="text-bb-dim">|</span>
          <span className={pnlColor(unrealized)}>UNREAL: {pnlSign(unrealized)}</span>
          <span className="text-bb-dim">|</span>
          <span className={heat > 0.35 ? "text-red-400" : heat > 0.2 ? "text-amber-400" : "text-[#888899]"}>
            HEAT: {(heat * 100).toFixed(1)}%
          </span>
          <span className="text-bb-dim">|</span>
          <span className="text-bb-white">BANKROLL: ${bankroll.toFixed(0)}</span>
        </div>
        <button
          onClick={toggleExecution}
          className={`text-[10px] px-3 py-0.5 border font-bold tracking-wider ${
            executionStatus?.paused
              ? "border-green-400/40 text-green-400 hover:bg-green-400/10"
              : "border-amber-400/40 text-amber-400 hover:bg-amber-400/10"
          }`}
        >
          {executionStatus?.paused ? "RESUME" : "PAUSE"}
        </button>
      </div>

      {/* ── Main 2x2 Grid ─────────────────────────────────────────────── */}
      <div className="flex-1 min-h-0 grid grid-cols-2 grid-rows-2" style={{ gap: 1, background: "#1a1a1a" }}>
        {/* Top-Left: Trade Blotter */}
        <div className="bg-bb-black flex flex-col overflow-hidden">
          <div className="bb-panel-title flex items-center justify-between">
            <span>TRADE BLOTTER</span>
            <span className="text-bb-dim text-[9px]">{openCount} open</span>
          </div>
          <div className="flex-1 overflow-y-auto">
            <table className="bb-table w-full">
              <thead className="sticky top-0 bg-bb-black z-10">
                <tr>
                  <th>TIME</th>
                  <th>TICKER</th>
                  <th>DIR</th>
                  <th style={{ textAlign: "right" }}>PNL</th>
                  <th style={{ textAlign: "right" }}></th>
                </tr>
              </thead>
              <tbody>
                {blotterRows.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="text-bb-dim text-center py-4">
                      NO TRADES
                    </td>
                  </tr>
                ) : (
                  blotterRows.map((row, i) => (
                    <tr key={i}>
                      <td className="text-bb-dim text-[9px]">
                        {fmtRelativeTime(row.time)}
                      </td>
                      <td
                        className="truncate"
                        style={{ maxWidth: 140 }}
                        title={row.ticker}
                      >
                        {row.ticker.length > 18
                          ? row.ticker.slice(0, 18) + "\u2026"
                          : row.ticker}
                      </td>
                      <td
                        className={
                          row.action === "BUY"
                            ? "text-green-400"
                            : row.action === "SELL"
                            ? "text-red-400"
                            : "text-amber-400"
                        }
                      >
                        {row.direction === "BUY_YES" ? "YES" : "NO"}
                      </td>
                      <td
                        style={{ textAlign: "right" }}
                        className={row.pnl >= 0 ? "text-green-400" : "text-red-400"}
                      >
                        {pnlSign(row.pnl)}
                      </td>
                      <td style={{ textAlign: "right" }}>
                        {row.status === "OPEN" && (
                          <button
                            onClick={() => {
                              if (confirm(`Close ${row.ticker}? Current P&L: ${pnlSign(row.pnl)}`)) {
                                closePosition(row.ticker);
                              }
                            }}
                            className="text-[8px] text-red-400 border border-red-400/30 px-1.5 py-0 hover:bg-red-400/10"
                          >
                            CLOSE
                          </button>
                        )}
                        {row.status === "CLOSED" && (
                          <span className="text-[8px] text-[#555566]">CLOSED</span>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Top-Right: AI Agent */}
        <div className="bg-bb-black flex flex-col overflow-hidden">
          <div className="bb-panel-title flex items-center justify-between">
            <span>AI AGENT</span>
            <span className="text-bb-dim text-[9px]">
              Cycle: {cycleCount} | Q-states: {rlStats.q_states}
            </span>
          </div>
          <div className="flex-1 overflow-y-auto p-2 space-y-2">
            {/* Pending Theses */}
            <div className="text-[10px] text-bb-orange tracking-wider font-bold mb-1">
              PENDING THESES
            </div>
            {Object.keys(pendingTheses).length === 0 ? (
              <div className="text-[10px] text-[#555566]">No pending theses</div>
            ) : (
              Object.entries(pendingTheses).map(([ticker, thesis]) => (
                <ThesisCard key={ticker} ticker={ticker} thesis={thesis} />
              ))
            )}

            {/* Decision Log */}
            <div className="text-[10px] text-bb-orange tracking-wider font-bold mt-3 mb-1 border-t border-bb-border pt-2">
              DECISION LOG
            </div>
            {recentDecisions.length === 0 ? (
              <div className="text-[10px] text-[#555566]">No decisions yet</div>
            ) : (
              recentDecisions.slice(0, 10).map((d, i) => (
                <div key={i} className="flex items-center gap-2 text-[10px]">
                  <span className="text-[#555566] w-[45px] shrink-0">
                    Cycle {d.cycle}
                  </span>
                  <span
                    className={
                      d.entries_executed > 0
                        ? "text-green-400"
                        : "text-[#888899]"
                    }
                  >
                    {d.entries_executed}{" "}
                    {d.entries_executed === 1 ? "entry" : "entries"}
                  </span>
                  <span className="text-[#555566]">
                    {d.theses_generated} theses
                  </span>
                  <span className="text-[#555566]">{d.skipped} skip</span>
                  {d.ts && (
                    <span className="text-[#333344] ml-auto text-[9px]">
                      {new Date(d.ts).toLocaleTimeString()}
                    </span>
                  )}
                </div>
              ))
            )}
          </div>
        </div>

        {/* Bottom-Left: Risk Metrics */}
        <div className="bg-bb-black flex flex-col overflow-hidden">
          <div className="bb-panel-title">RISK METRICS</div>
          <div className="p-3 flex flex-col gap-[4px] text-[11px]">
            <RiskRow
              label="TOTAL P&L"
              value={pnlSign(pnl)}
              color={pnl >= 0 ? "#00ff00" : "#ff0000"}
            />
            <RiskRow
              label="REALIZED"
              value={pnlSign(realized)}
              color={realized >= 0 ? "#00ff00" : "#ff0000"}
            />
            <RiskRow
              label="UNREALIZED"
              value={pnlSign(unrealized)}
              color={unrealized >= 0 ? "#00ff00" : "#ff0000"}
            />
            <div className="border-b border-bb-border my-[2px]" />
            <RiskRow label="OPEN POS" value={String(openCount)} />
            <RiskRow label="EXPOSURE" value={`$${deployed.toFixed(0)}`} />
            <RiskRow
              label="HEAT"
              value={`${(heat * 100).toFixed(1)}%`}
              color={
                heat > 0.35
                  ? "#ff0000"
                  : heat > 0.2
                  ? "#ffff00"
                  : "#ffffff"
              }
            />
            <div className="border-b border-bb-border my-[2px]" />
            <RiskRow
              label="ENGINE"
              value={engineActive ? "ACTIVE" : "PAUSED"}
              color={engineActive ? "#00ff00" : "#ffff00"}
            />
            <RiskRow
              label="BANKROLL"
              value={`$${bankroll.toFixed(0)}`}
            />
          </div>
        </div>

        {/* Bottom-Right: Live Events */}
        <div className="bg-bb-black flex flex-col overflow-hidden">
          <div className="bb-panel-title flex items-center justify-between">
            <span>LIVE EVENTS</span>
            <span className="text-bb-dim text-[9px]">{feedEvents.length}</span>
          </div>
          <div className="flex-1 overflow-y-auto">
            {recentEvents.length === 0 ? (
              <div className="text-bb-dim text-[10px] p-2">
                WAITING FOR EVENTS...
              </div>
            ) : (
              recentEvents.map((ev) => (
                <div
                  key={ev.seq}
                  className="flex items-baseline gap-2 px-2 py-[1px] text-[10px] hover:bg-[#1e1e2e]/30"
                >
                  <span className="text-bb-dim text-[9px] w-[40px] shrink-0 text-right">
                    {fmtRelativeTime(ev.ts)}
                  </span>
                  <span
                    className={
                      ev.event_type === "TRADE"
                        ? "text-green-400"
                        : ev.event_type === "SIGNAL_CHANGE"
                        ? "text-amber-400"
                        : ev.event_type === "REGIME_CHANGE"
                        ? "text-blue-400"
                        : ev.event_type === "ERROR"
                        ? "text-red-400"
                        : "text-bb-dim"
                    }
                    style={{ width: 70, flexShrink: 0 }}
                  >
                    [{ev.event_type.replace(/_/g, " ")}]
                  </span>
                  <span className="text-bb-white truncate">{ev.message}</span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Sub-components ──────────────────────────────────────────────────────────

function RiskRow({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div className="flex justify-between">
      <span className="text-bb-orange">{label}</span>
      <span style={color ? { color } : { color: "#ffffff" }}>{value}</span>
    </div>
  );
}

function ThesisCard({
  ticker,
  thesis,
}: {
  ticker: string;
  thesis: Record<string, unknown>;
}) {
  const direction = (thesis.direction as string) ?? "";
  const edge = (thesis.edge as number) ?? 0;
  const conviction = (thesis.conviction as number) ?? 0;
  const confidenceReasons = (thesis.confidence_reasons as string[]) ?? [];
  const riskFactors = (thesis.risk_factors as string[]) ?? [];
  const feeImpact = (thesis.fee_impact as number) ?? 0;

  return (
    <div className="border border-[#222233] bg-[#08080e] p-2 text-[10px]">
      <div className="flex items-center justify-between mb-1">
        <span className="text-bb-white font-bold">
          {ticker} {direction}
        </span>
        <span className="text-[9px] text-[#555566]">
          conv={conviction.toFixed(2)} | edge=
          <span className={edge >= 0 ? "text-green-400" : "text-red-400"}>
            {edge >= 0 ? "+" : ""}
            {(edge * 100).toFixed(1)}c
          </span>
        </span>
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5">
        {confidenceReasons.slice(0, 3).map((r, i) => (
          <span key={i} className="text-green-400">
            {"\u2713"} {typeof r === "string" ? r : String(r)}
          </span>
        ))}
        {riskFactors.slice(0, 3).map((r, i) => (
          <span key={i} className="text-amber-400">
            {"\u26A0"} {typeof r === "string" ? r : String(r)}
          </span>
        ))}
        {feeImpact > 0.3 && (
          <span className="text-red-400">
            {"\u26A0"} Fees {(feeImpact * 100).toFixed(0)}% of edge
          </span>
        )}
      </div>
    </div>
  );
}
