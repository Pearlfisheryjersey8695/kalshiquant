"use client";

import { useDashboard } from "@/lib/store";
import { api } from "@/lib/api";
import { useState, useEffect, useMemo } from "react";
import { Group, Panel, Separator } from "react-resizable-panels";
import BBMarketScanner from "./BBMarketScanner";
import BBPriceChart from "./BBPriceChart";
import BBSignalDetail from "./BBSignalDetail";
import type { Signal, ArbitrageOpportunity } from "@/lib/types";

// ── Helpers ─────────────────────────────────────────────────────────────────

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

/** Count how many signal sources agree: fair_value, price_predictor, sentiment */
function agreementScore(sig: Signal): { fv: boolean; pred: boolean; sent: boolean; total: number } {
  const dir = sig.direction;

  // Fair value agrees if edge direction matches trade direction
  const fv = dir === "BUY_YES" ? sig.edge > 0.01 : sig.edge < -0.01;

  // Predictor agrees if prediction direction matches trade direction
  const pred_dir = sig.predicted_change || 0;
  const pred = dir === "BUY_YES" ? pred_dir > 0 : pred_dir < 0;

  // Sentiment agrees if sentiment edge aligns with direction
  const sent_edge = sig.sentiment_edge ?? sig.consensus_edge ?? 0;
  const sent = dir === "BUY_YES" ? sent_edge > 0.01 : sent_edge < -0.01;

  return { fv, pred, sent, total: (fv ? 1 : 0) + (pred ? 1 : 0) + (sent ? 1 : 0) };
}

// ── Resize Handle ───────────────────────────────────────────────────────────

function Handle() {
  return <Separator />;
}

// ── Main Component ──────────────────────────────────────────────────────────

export default function BBScanner() {
  const { signals } = useDashboard();
  const [arbitrage, setArbitrage] = useState<ArbitrageOpportunity[]>([]);

  useEffect(() => {
    api.getArbitrage().then(setArbitrage).catch(console.error);
    const id = setInterval(() => {
      api.getArbitrage().then(setArbitrage).catch(() => {});
    }, 30000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="h-full flex flex-col bg-[#0a0a0f] font-mono text-[13px]">
      {/* Header */}
      <div className="h-[32px] bg-[#12121a] border-b border-[#1e1e2e] flex items-center px-4 shrink-0">
        <span className="text-bb-orange text-[13px] font-bold tracking-wider">F2 SCANNER</span>
        <span className="text-[#888899] text-[11px] ml-3">Market Analysis Workspace</span>
        <span className="text-[#888899] text-[11px] ml-auto">{signals.length} signals</span>
      </div>

      {/* Main resizable layout */}
      <Group orientation="vertical" className="flex-1 min-h-0">
        {/* Top 80%: Scanner | Chart | Signal Detail */}
        <Panel defaultSize={78} minSize={50}>
          <Group orientation="horizontal" className="h-full">
            <Panel defaultSize={30} minSize={20}>
              <BBMarketScanner />
            </Panel>
            <Handle />
            <Panel defaultSize={45} minSize={25}>
              <BBPriceChart />
            </Panel>
            <Handle />
            <Panel defaultSize={25} minSize={15}>
              <BBSignalDetail />
            </Panel>
          </Group>
        </Panel>

        <Handle />

        {/* Bottom 22%: Signal Agreement | Arbitrage Scanner */}
        <Panel defaultSize={22} minSize={12}>
          <Group orientation="horizontal" className="h-full">
            <Panel defaultSize={55} minSize={30}>
              <SignalAgreementPanel signals={signals} />
            </Panel>
            <Handle />
            <Panel defaultSize={45} minSize={25}>
              <ArbitrageScannerPanel data={arbitrage} />
            </Panel>
          </Group>
        </Panel>
      </Group>
    </div>
  );
}

// ── Signal Agreement Panel ──────────────────────────────────────────────────

function SignalAgreementPanel({ signals }: { signals: Signal[] }) {
  // Sort by agreement score desc, then edge desc
  const sorted = useMemo(() => {
    return [...signals]
      .map((sig) => ({ sig, score: agreementScore(sig) }))
      .sort((a, b) => {
        if (b.score.total !== a.score.total) return b.score.total - a.score.total;
        return Math.abs(b.sig.edge) - Math.abs(a.sig.edge);
      });
  }, [signals]);

  return (
    <div className="h-full flex flex-col overflow-hidden bg-[#0a0a0f]">
      <div className="h-[24px] bg-[#12121a] border-b border-[#1e1e2e] flex items-center justify-between px-3 shrink-0">
        <span className="text-bb-orange text-[10px] tracking-wider font-bold">SIGNAL AGREEMENT</span>
        <span className="text-[10px] text-[#888899]">{signals.length}</span>
      </div>
      <div className="flex-1 overflow-y-auto">
        {sorted.length === 0 ? (
          <div className="px-4 py-6 text-[11px] text-[#888899] text-center">NO SIGNALS AVAILABLE</div>
        ) : (
          <table className="w-full text-[11px]">
            <thead className="sticky top-0 bg-[#12121a]">
              <tr className="text-[#888899] border-b border-[#1e1e2e]">
                <th className="text-left px-2 py-1 font-normal">TICKER</th>
                <th className="text-center px-2 py-1 font-normal">DIR</th>
                <th className="text-right px-2 py-1 font-normal">EDGE</th>
                <th className="text-right px-2 py-1 font-normal">CONF</th>
                <th className="text-center px-1 py-1 font-normal">FV</th>
                <th className="text-center px-1 py-1 font-normal">PRED</th>
                <th className="text-center px-1 py-1 font-normal">SENT</th>
                <th className="text-center px-2 py-1 font-normal">AGREE</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map(({ sig, score }) => {
                const allAgree = score.total === 3;

                return (
                  <tr key={sig.ticker} className="border-b border-[#1e1e2e]/50 hover:bg-[#1a1a2a] transition-colors">
                    <td className="px-2 py-1 text-[#e0e0e0] whitespace-nowrap truncate max-w-[140px]" title={sig.title}>
                      {sig.ticker.length > 18 ? sig.ticker.slice(0, 18) + "\u2026" : sig.ticker}
                    </td>
                    <td className={`px-2 py-1 text-center font-bold ${dirColor(sig.direction)}`}>
                      {dirLabel(sig.direction)}
                    </td>
                    <td className={`px-2 py-1 text-right ${edgeColor(sig.edge)}`}>
                      {sig.edge >= 0 ? "+" : ""}{(sig.edge * 100).toFixed(1)}c
                    </td>
                    <td className="px-2 py-1 text-right text-[#e0e0e0]">
                      {(sig.confidence * 100).toFixed(0)}%
                    </td>
                    <td className="px-1 py-1 text-center">
                      <span className={score.fv ? "text-green-400" : "text-red-400"}>
                        {score.fv ? "\u2713" : "\u2717"}
                      </span>
                    </td>
                    <td className="px-1 py-1 text-center">
                      <span className={score.pred ? "text-green-400" : "text-red-400"}>
                        {score.pred ? "\u2713" : "\u2717"}
                      </span>
                    </td>
                    <td className="px-1 py-1 text-center">
                      <span className={score.sent ? "text-green-400" : "text-red-400"}>
                        {score.sent ? "\u2713" : "\u2717"}
                      </span>
                    </td>
                    <td className="px-2 py-1 text-center">
                      <span className={
                        allAgree ? "text-green-400 font-bold" :
                        score.total >= 2 ? "text-amber-400" :
                        "text-red-400"
                      }>
                        {score.total}/3
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ── Arbitrage Scanner Panel ─────────────────────────────────────────────────

function ArbitrageScannerPanel({ data }: { data: ArbitrageOpportunity[] }) {
  return (
    <div className="h-full flex flex-col overflow-hidden bg-[#0a0a0f]">
      <div className="h-[24px] bg-[#12121a] border-b border-[#1e1e2e] flex items-center justify-between px-3 shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-bb-orange text-[10px] tracking-wider font-bold">ARBITRAGE SCANNER</span>
          {data.length > 0 && (
            <span className="text-[9px] px-1.5 py-0.5 bg-green-500/20 text-green-400 border border-green-500/40 rounded">
              {data.length} FOUND
            </span>
          )}
        </div>
      </div>
      <div className="flex-1 overflow-y-auto">
        {data.length === 0 ? (
          <div className="px-4 py-6 text-[11px] text-[#888899] text-center">
            NO ARBITRAGE OPPORTUNITIES DETECTED
          </div>
        ) : (
          <table className="w-full text-[11px]">
            <thead className="sticky top-0 bg-[#12121a]">
              <tr className="text-[#888899] border-b border-[#1e1e2e]">
                <th className="text-left px-2 py-1 font-normal">TYPE</th>
                <th className="text-left px-2 py-1 font-normal">BUY</th>
                <th className="text-left px-2 py-1 font-normal">SELL</th>
                <th className="text-right px-2 py-1 font-normal">BUY $</th>
                <th className="text-right px-2 py-1 font-normal">SELL $</th>
                <th className="text-right px-2 py-1 font-normal">EDGE</th>
                <th className="text-left px-2 py-1 font-normal">DESC</th>
              </tr>
            </thead>
            <tbody>
              {data.map((arb, i) => (
                <tr key={i} className="border-b border-[#1e1e2e]/50 hover:bg-[#1a1a2a] transition-colors">
                  <td className="px-2 py-1 text-amber-400 whitespace-nowrap">{arb.type}</td>
                  <td className="px-2 py-1 text-green-400 whitespace-nowrap">
                    {arb.buy_ticker.length > 16 ? arb.buy_ticker.slice(0, 16) + "\u2026" : arb.buy_ticker}
                  </td>
                  <td className="px-2 py-1 text-red-400 whitespace-nowrap">
                    {arb.sell_ticker.length > 16 ? arb.sell_ticker.slice(0, 16) + "\u2026" : arb.sell_ticker}
                  </td>
                  <td className="px-2 py-1 text-right text-[#e0e0e0]">{(arb.buy_price * 100).toFixed(0)}c</td>
                  <td className="px-2 py-1 text-right text-[#e0e0e0]">{(arb.sell_price * 100).toFixed(0)}c</td>
                  <td className={`px-2 py-1 text-right font-bold ${edgeColor(arb.edge)}`}>
                    {arb.edge >= 0 ? "+" : ""}{(arb.edge * 100).toFixed(1)}c
                  </td>
                  <td className="px-2 py-1 text-[#888899] max-w-[200px] truncate">{arb.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
