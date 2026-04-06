"use client";

import { useDashboard } from "@/lib/store";
import { fmtRelativeTime } from "@/lib/format";
import PanelHeader from "./PanelHeader";
import { useRef, useEffect } from "react";

const EVENT_STYLES: Record<string, { color: string; icon: string }> = {
  PRICE_MOVE: { color: "text-blue", icon: "\u2191\u2193" },
  SIGNAL_CHANGE: { color: "text-amber", icon: "\u26A1" },
  REGIME_CHANGE: { color: "text-[#a855f7]", icon: "\u25C6" },
  TRADE: { color: "text-green", icon: "\u2713" },
  CONNECTION: { color: "text-text-secondary", icon: "\u25CF" },
  ERROR: { color: "text-red", icon: "\u2716" },
};

export default function LiveFeed() {
  const { feedEvents } = useDashboard();
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = 0;
    }
  }, [feedEvents.length]);

  const sorted = [...feedEvents].sort((a, b) => b.seq - a.seq);

  return (
    <div className="flex flex-col h-full">
      <PanelHeader title="Live Feed" subtitle={`${feedEvents.length} events`} />
      <div ref={scrollRef} className="flex-1 overflow-y-auto min-h-0">
        {sorted.length === 0 ? (
          <div className="flex items-center justify-center h-full text-text-secondary text-sm">
            Waiting for events...
          </div>
        ) : (
          <div className="divide-y divide-border/50">
            {sorted.map((event) => {
              const style = EVENT_STYLES[event.event_type] || EVENT_STYLES.CONNECTION;
              return (
                <div key={event.seq} className="px-3 py-1.5 hover:bg-border/20 transition-colors">
                  <div className="flex items-start gap-2">
                    <span className={`${style.color} text-[10px] mt-0.5 w-3 text-center shrink-0`}>
                      {style.icon}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className={`text-[9px] font-mono font-bold ${style.color} uppercase`}>
                          {event.event_type.replace(/_/g, " ")}
                        </span>
                        {event.ticker && (
                          <span className="text-[9px] font-mono text-text-secondary">{event.ticker}</span>
                        )}
                        <span className="text-[9px] text-text-secondary ml-auto shrink-0">
                          {fmtRelativeTime(event.ts)}
                        </span>
                      </div>
                      <div className="text-[10px] text-text-primary truncate">{event.message}</div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
