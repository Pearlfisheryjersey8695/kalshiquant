"use client";

import { api } from "@/lib/api";
import { useState, useEffect, useCallback } from "react";
import type { BrainLesson } from "@/lib/types";
import BBPerformance from "./BBPerformance";
import BBTradeJournal from "./BBTradeJournal";

// ── Helpers ─────────────────────────────────────────────────────────────────

function pnlColor(v: number) {
  return v > 0 ? "text-green-400" : v < 0 ? "text-red-400" : "text-[#888899]";
}

function pnlSign(v: number) {
  return v >= 0 ? `+$${v.toFixed(2)}` : `-$${Math.abs(v).toFixed(2)}`;
}

// ── Component ───────────────────────────────────────────────────────────────

export default function BBReview() {
  const [lessons, setLessons] = useState<BrainLesson[]>([]);
  const [rlPolicy, setRlPolicy] = useState<Record<string, { q_trade: number; count: number }>>({});
  const [section, setSection] = useState<"performance" | "journal">("performance");
  const [loading, setLoading] = useState(true);

  const fetchRL = useCallback(() => {
    Promise.all([
      api.getBrainLessons(),
      api.getBrainPolicy(),
    ])
      .then(([l, p]) => {
        setLessons(l as unknown as BrainLesson[]);
        setRlPolicy(p as Record<string, { q_trade: number; count: number }>);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    fetchRL();
    const id = setInterval(fetchRL, 15000);
    return () => clearInterval(id);
  }, [fetchRL]);

  return (
    <div className="h-full flex flex-col overflow-hidden bg-bb-black font-mono">
      {/* ── Header + Section Toggle ──────────────────────────────────── */}
      <div className="h-[34px] bg-[#12121a] border-b border-bb-border flex items-center justify-between px-4 shrink-0">
        <span className="text-bb-orange text-[11px] font-bold tracking-wider">
          F6 REVIEW
        </span>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setSection("performance")}
            className={`px-3 py-0.5 text-[10px] border ${
              section === "performance"
                ? "text-bb-orange border-bb-orange/40 bg-bb-orange/10"
                : "text-bb-dim border-transparent hover:text-bb-white"
            }`}
          >
            PERFORMANCE
          </button>
          <button
            onClick={() => setSection("journal")}
            className={`px-3 py-0.5 text-[10px] border ${
              section === "journal"
                ? "text-bb-orange border-bb-orange/40 bg-bb-orange/10"
                : "text-bb-dim border-transparent hover:text-bb-white"
            }`}
          >
            TRADE JOURNAL
          </button>
        </div>
      </div>

      {/* ── Main Content ─────────────────────────────────────────────── */}
      <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
        {/* Performance or Journal section (takes ~70% height) */}
        <div className="flex-[3] min-h-0 overflow-hidden">
          {section === "performance" ? <BBPerformance /> : <BBTradeJournal />}
        </div>

        {/* RL Footer: Lessons + Policy (takes ~30% height) */}
        <div
          className="flex-[1] min-h-0 border-t border-bb-border grid grid-cols-2"
          style={{ gap: 1, background: "#1a1a1a" }}
        >
          {/* RL Lessons Learned */}
          <div className="bg-bb-black flex flex-col overflow-hidden">
            <div className="bb-panel-title">RL LESSONS</div>
            <div className="flex-1 overflow-y-auto p-2 space-y-1">
              {loading ? (
                <div className="text-[10px] text-[#555566] animate-pulse">
                  Loading RL data...
                </div>
              ) : lessons.length === 0 ? (
                <div className="text-[10px] text-[#555566]">
                  No lessons yet -- trades must close first
                </div>
              ) : (
                lessons.map((l, i) => (
                  <div
                    key={i}
                    className="flex items-center gap-2 text-[10px]"
                  >
                    <span className="w-[70px] shrink-0 text-[#ccccdd] truncate">
                      {l.ticker}
                    </span>
                    <span
                      className={`w-[55px] shrink-0 font-bold ${pnlColor(
                        l.pnl
                      )}`}
                    >
                      {pnlSign(l.pnl)}
                    </span>
                    <span className="text-[#888899] truncate flex-1">
                      &quot;{l.lesson}&quot;
                    </span>
                    {l.thesis_correct ? (
                      <span className="text-green-400 text-[9px] shrink-0">
                        THESIS OK
                      </span>
                    ) : l.pnl < 0 ? (
                      <span className="text-red-400 text-[9px] shrink-0">
                        THESIS WRONG
                      </span>
                    ) : null}
                  </div>
                ))
              )}
            </div>
          </div>

          {/* RL Policy Map */}
          <div className="bg-bb-black flex flex-col overflow-hidden">
            <div className="bb-panel-title">RL POLICY MAP</div>
            <div className="flex-1 overflow-y-auto p-2 space-y-1">
              {loading ? (
                <div className="text-[10px] text-[#555566] animate-pulse">
                  Loading policy...
                </div>
              ) : Object.keys(rlPolicy).length === 0 ? (
                <div className="text-[10px] text-[#555566]">
                  No RL states recorded yet
                </div>
              ) : (
                Object.entries(rlPolicy)
                  .sort((a, b) => (b[1].count ?? 0) - (a[1].count ?? 0))
                  .map(([stateKey, data]) => (
                    <div
                      key={stateKey}
                      className="flex items-center gap-2 text-[10px]"
                    >
                      <span
                        className="text-[#888899] font-mono truncate"
                        style={{ maxWidth: "55%" }}
                      >
                        {stateKey}
                      </span>
                      <span
                        className={`shrink-0 ${pnlColor(
                          data.q_trade ?? 0
                        )}`}
                      >
                        Q=
                        {data.q_trade >= 0 ? "+" : ""}
                        {(data.q_trade ?? 0).toFixed(3)}
                      </span>
                      <span className="text-[#555566] shrink-0">
                        ({data.count ?? 0}{" "}
                        {(data.count ?? 0) === 1 ? "trade" : "trades"})
                      </span>
                    </div>
                  ))
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
