"use client";

import { DashboardProvider, useDashboard } from "@/lib/store";
import BBHeader from "@/components/BBHeader";
import BBTabBar from "@/components/BBTabBar";
import type { TabId } from "@/components/BBTabBar";
import BBCommandPalette from "@/components/BBCommandPalette";
import BBIntel from "@/components/BBIntel";
import BBScanner from "@/components/BBScanner";
import BBAnalytics from "@/components/BBAnalytics";
import BBExecute from "@/components/BBExecute";
import BBRiskEngine from "@/components/BBRiskEngine";
import BBReview from "@/components/BBReview";
import { useState, useEffect, useCallback } from "react";

const FKEY_MAP: Record<string, TabId> = {
  F1: "intel",
  F2: "scanner",
  F3: "analytics",
  F4: "execute",
  F5: "risk",
  F6: "review",
};

export default function Dashboard() {
  const [cmdOpen, setCmdOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<TabId>("intel");

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    // Ctrl+K command palette
    if ((e.metaKey || e.ctrlKey) && e.key === "k") {
      e.preventDefault();
      setCmdOpen((v) => !v);
      return;
    }
    if (e.key === "Escape") { setCmdOpen(false); return; }

    // F1-F6 tab switching
    const tab = FKEY_MAP[e.key];
    if (tab) {
      e.preventDefault();
      setActiveTab(tab);
    }
  }, []);

  useEffect(() => {
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handleKeyDown]);

  return (
    <DashboardProvider>
      <div className="h-screen w-screen flex flex-col overflow-hidden bg-bb-black">
        <BBHeader />
        <BBTabBar activeTab={activeTab} onTabChange={setActiveTab} />
        <ConnectionStatus />

        {/* Tab content area */}
        <div className="flex-1 min-h-0">
          {activeTab === "intel" && <BBIntel />}
          {activeTab === "scanner" && <BBScanner />}
          {activeTab === "analytics" && <BBAnalytics />}
          {activeTab === "execute" && <BBExecute />}
          {activeTab === "risk" && <BBRiskEngine />}
          {activeTab === "review" && <BBReview />}
        </div>

        {/* Keyboard hint */}
        <div className="h-[14px] bg-bb-header border-t border-bb-border flex items-center justify-between px-2 shrink-0">
          <span className="text-[9px] text-bb-dim">F1-F6 TABS</span>
          <span className="text-[9px] text-bb-dim">CTRL+K CMD | ESC CLOSE</span>
        </div>
      </div>
      {cmdOpen && <BBCommandPalette onClose={() => setCmdOpen(false)} />}
    </DashboardProvider>
  );
}

function ConnectionStatus() {
  const { loading, connectionError } = useDashboard();

  if (loading) {
    return (
      <div className="h-8 bg-[#12121a] border-b border-bb-border flex items-center justify-center">
        <span className="text-[11px] text-bb-dim animate-pulse">Connecting to KalshiQuant server...</span>
      </div>
    );
  }

  if (connectionError) {
    return (
      <div className="h-8 bg-red/10 border-b border-red/30 flex items-center justify-center gap-2">
        <span className="text-[11px] text-red font-mono">{connectionError}</span>
        <button
          onClick={() => window.location.reload()}
          className="text-[10px] text-red border border-red/30 px-2 py-0.5 hover:bg-red/10"
        >
          RETRY
        </button>
      </div>
    );
  }

  return null;
}
