"use client";

import { api } from "@/lib/api";
import { useState, useEffect, useCallback } from "react";
import type { BrainStatus, BrainDecision, BrainLesson } from "@/lib/types";

// ── Helpers ─────────────────────────────────────────────────────────────────

function pnlColor(v: number) {
  return v > 0 ? "text-green-400" : v < 0 ? "text-red-400" : "text-[#888899]";
}

function pnlSign(v: number) {
  return v >= 0 ? `+$${v.toFixed(2)}` : `-$${Math.abs(v).toFixed(2)}`;
}

function pctFmt(v: number) {
  return `${(v * 100).toFixed(1)}%`;
}

// ── Component ───────────────────────────────────────────────────────────────

export default function BBQuantBrain() {
  const [status, setStatus] = useState<BrainStatus | null>(null);
  const [decisions, setDecisions] = useState<BrainDecision[]>([]);
  const [theses, setTheses] = useState<Record<string, Record<string, unknown>>>({});
  const [lessons, setLessons] = useState<BrainLesson[]>([]);
  const [rlPolicy, setRlPolicy] = useState<Record<string, { q_trade: number; count: number }>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const fetchData = useCallback(() => {
    Promise.all([
      api.getBrainStatus(),
      api.getBrainDecisions(20),
      api.getBrainTheses(),
      api.getBrainLessons(),
      api.getBrainPolicy(),
    ])
      .then(([s, d, t, l, p]) => {
        setStatus(s as unknown as BrainStatus);
        setDecisions(d as unknown as BrainDecision[]);
        setTheses(t as Record<string, Record<string, unknown>>);
        setLessons(l as unknown as BrainLesson[]);
        setRlPolicy(p as Record<string, { q_trade: number; count: number }>);
        setError("");
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    fetchData();
    const iv = setInterval(fetchData, 10_000);
    return () => clearInterval(iv);
  }, [fetchData]);

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center bg-bb-black">
        <span className="text-bb-dim text-[13px] animate-pulse font-mono">Loading QuantBrain...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="h-full flex items-center justify-center bg-bb-black">
        <span className="text-red-400 text-[13px] font-mono">ERROR: {error}</span>
      </div>
    );
  }

  const isActive = status?.active ?? false;
  const cycleCount = status?.cycle_count ?? 0;
  const rlStats = status?.rl_stats ?? { total_experiences: 0, q_states: 0, exploration_rate: 0, n_updates: 0 };
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const perception = (status as any)?.perception as Record<string, unknown> | undefined;
  const trackedMarkets = (perception?.tracked_markets as number) ?? 0;
  const activeSignals = (perception?.active_signals as number) ?? 0;

  // Count total trades from decisions
  const totalTrades = decisions.reduce((sum, d) => sum + (d.entries_executed ?? 0), 0);

  return (
    <div className="h-full overflow-auto bg-bb-black p-3 font-mono text-[12px]">
      {/* Header bar */}
      <div className="flex items-center justify-between mb-3 border-b border-bb-border pb-2">
        <div className="flex items-center gap-3">
          <span className="text-bb-orange font-bold text-[14px] tracking-wider">F9 QUANT BRAIN</span>
          <span className="text-[#888899]">Autonomous Trading Agent</span>
        </div>
        <span
          className={`px-2 py-0.5 text-[11px] font-bold tracking-wider border ${
            isActive
              ? "text-green-400 border-green-400/30 bg-green-400/5"
              : "text-red-400 border-red-400/30 bg-red-400/5"
          }`}
        >
          {isActive ? "ACTIVE" : "INACTIVE"}
        </span>
      </div>

      {/* Summary metrics strip */}
      <div className="grid grid-cols-6 gap-2 mb-3">
        <MetricBox label="CYCLE" value={String(cycleCount)} />
        <MetricBox label="TRADES" value={String(totalTrades)} />
        <MetricBox label="Q-STATES" value={String(rlStats.q_states)} />
        <MetricBox label="EXPL%" value={pctFmt(rlStats.exploration_rate)} />
        <MetricBox label="EXPERIENCES" value={String(rlStats.total_experiences)} />
        <MetricBox label="PERCEPTION" value={`${trackedMarkets} mkts | ${activeSignals} sigs`} small />
      </div>

      {/* Main content: two columns */}
      <div className="grid grid-cols-2 gap-3 mb-3" style={{ minHeight: "220px" }}>
        {/* Pending Theses */}
        <div className="border border-bb-border bg-[#0a0a12] p-2">
          <div className="text-bb-orange text-[11px] font-bold mb-2 tracking-wider border-b border-bb-border pb-1">
            PENDING THESES
          </div>
          <div className="space-y-2 max-h-[240px] overflow-auto">
            {Object.keys(theses).length === 0 && (
              <span className="text-[#555566] text-[11px]">No pending theses</span>
            )}
            {Object.entries(theses).map(([ticker, thesis]) => (
              <ThesisCard key={ticker} ticker={ticker} thesis={thesis} />
            ))}
          </div>
        </div>

        {/* Decision Log */}
        <div className="border border-bb-border bg-[#0a0a12] p-2">
          <div className="text-bb-orange text-[11px] font-bold mb-2 tracking-wider border-b border-bb-border pb-1">
            DECISION LOG
          </div>
          <div className="space-y-1 max-h-[240px] overflow-auto">
            {decisions.length === 0 && (
              <span className="text-[#555566] text-[11px]">No decisions yet</span>
            )}
            {decisions.map((d, i) => (
              <div key={i} className="flex items-center gap-2 text-[11px]">
                <span className="text-[#555566] w-[50px] shrink-0">Cycle {d.cycle}</span>
                <span className={d.entries_executed > 0 ? "text-green-400" : "text-[#888899]"}>
                  {d.entries_executed} {d.entries_executed === 1 ? "entry" : "entries"}
                </span>
                <span className="text-[#555566]">{d.skipped} skip</span>
                <span className="text-[#444455]">{d.elapsed_ms}ms</span>
                <span className="text-[#333344] ml-auto text-[10px]">
                  {d.ts ? new Date(d.ts).toLocaleTimeString() : ""}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Lessons Learned */}
      <div className="border border-bb-border bg-[#0a0a12] p-2 mb-3">
        <div className="text-bb-orange text-[11px] font-bold mb-2 tracking-wider border-b border-bb-border pb-1">
          LESSONS LEARNED (from RL)
        </div>
        <div className="space-y-1 max-h-[160px] overflow-auto">
          {lessons.length === 0 && (
            <span className="text-[#555566] text-[11px]">No lessons yet — trades must close first</span>
          )}
          {lessons.map((l, i) => (
            <div key={i} className="flex items-center gap-2 text-[11px]">
              <span className="w-[80px] shrink-0 text-[#ccccdd] truncate">{l.ticker}</span>
              <span className={`w-[60px] shrink-0 font-bold ${pnlColor(l.pnl)}`}>
                {pnlSign(l.pnl)}
              </span>
              <span className="text-[#888899] truncate">
                &quot;{l.lesson}&quot;
              </span>
              {l.thesis_correct && (
                <span className="text-green-400 text-[10px] shrink-0">THESIS OK</span>
              )}
              {!l.thesis_correct && l.pnl < 0 && (
                <span className="text-red-400 text-[10px] shrink-0">THESIS WRONG</span>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* RL Policy Map */}
      <div className="border border-bb-border bg-[#0a0a12] p-2">
        <div className="text-bb-orange text-[11px] font-bold mb-2 tracking-wider border-b border-bb-border pb-1">
          RL POLICY MAP
        </div>
        <div className="space-y-1 max-h-[180px] overflow-auto">
          {Object.keys(rlPolicy).length === 0 && (
            <span className="text-[#555566] text-[11px]">No RL states recorded yet</span>
          )}
          {Object.entries(rlPolicy)
            .sort((a, b) => (b[1].count ?? 0) - (a[1].count ?? 0))
            .map(([stateKey, data]) => (
              <div key={stateKey} className="flex items-center gap-2 text-[11px]">
                <span className="text-[#888899] font-mono truncate" style={{ maxWidth: "55%" }}>
                  State: {stateKey}
                </span>
                <span className={`shrink-0 ${pnlColor(data.q_trade ?? 0)}`}>
                  Q={data.q_trade >= 0 ? "+" : ""}{(data.q_trade ?? 0).toFixed(3)}
                </span>
                <span className="text-[#555566] shrink-0">
                  ({data.count ?? 0} {(data.count ?? 0) === 1 ? "trade" : "trades"})
                </span>
              </div>
            ))}
        </div>
      </div>
    </div>
  );
}

// ── Sub-components ──────────────────────────────────────────────────────────

function MetricBox({ label, value, small }: { label: string; value: string; small?: boolean }) {
  return (
    <div className="border border-bb-border bg-[#0a0a12] p-2 text-center">
      <div className="text-[10px] text-[#555566] tracking-wider mb-1">{label}</div>
      <div className={`text-bb-white font-bold ${small ? "text-[11px]" : "text-[14px]"}`}>{value}</div>
    </div>
  );
}

function ThesisCard({ ticker, thesis }: { ticker: string; thesis: Record<string, unknown> }) {
  const direction = (thesis.direction as string) ?? "";
  const edge = (thesis.edge as number) ?? 0;
  const conviction = (thesis.conviction as number) ?? 0;
  const thesisText = (thesis.thesis as string) ?? "";
  const confidenceReasons = (thesis.confidence_reasons as string[]) ?? [];
  const riskFactors = (thesis.risk_factors as string[]) ?? [];
  const feeImpact = (thesis.fee_impact as number) ?? 0;

  return (
    <div className="border border-[#222233] bg-[#08080e] p-2">
      <div className="flex items-center justify-between mb-1">
        <span className="text-bb-white font-bold">{ticker}: {direction}</span>
        <span className="text-[10px] text-[#555566]">
          Edge: <span className={pnlColor(edge)}>{edge >= 0 ? "+" : ""}{edge.toFixed(4)}</span>
          {" | Conv: "}
          <span className="text-bb-white">{conviction.toFixed(2)}</span>
        </span>
      </div>
      {thesisText && (
        <div className="text-[11px] text-[#888899] italic mb-1 leading-tight">
          &quot;{thesisText.length > 120 ? thesisText.slice(0, 120) + "..." : thesisText}&quot;
        </div>
      )}
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[10px]">
        {confidenceReasons.slice(0, 3).map((r, i) => (
          <span key={i} className="text-green-400">
            {"\u2713"} {typeof r === "string" ? r : String(r)}
          </span>
        ))}
        {riskFactors.slice(0, 2).map((r, i) => (
          <span key={i} className="text-bb-orange">
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
