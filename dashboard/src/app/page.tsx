"use client";

import { DashboardProvider } from "@/lib/store";
import Header from "@/components/Header";
import MarketScanner from "@/components/MarketScanner";
import PriceChart from "@/components/PriceChart";
import SignalDetails from "@/components/SignalDetails";
import RiskDashboard from "@/components/RiskDashboard";
import TradeBlotter from "@/components/TradeBlotter";
import LiveFeed from "@/components/LiveFeed";
import PortfolioPanel from "@/components/PortfolioPanel";
import { useState } from "react";

function TabbedPanel() {
  const [tab, setTab] = useState<"risk" | "portfolio">("risk");
  return (
    <div className="flex flex-col h-full">
      <div className="flex border-b border-border shrink-0">
        <button
          onClick={() => setTab("risk")}
          className={`px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider transition-colors ${
            tab === "risk"
              ? "text-blue border-b-2 border-blue"
              : "text-text-secondary hover:text-text-primary"
          }`}
        >
          Risk
        </button>
        <button
          onClick={() => setTab("portfolio")}
          className={`px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider transition-colors ${
            tab === "portfolio"
              ? "text-amber border-b-2 border-amber"
              : "text-text-secondary hover:text-text-primary"
          }`}
        >
          Portfolio
        </button>
      </div>
      <div className="flex-1 min-h-0 overflow-hidden">
        {tab === "risk" ? <RiskDashboard /> : <PortfolioPanel />}
      </div>
    </div>
  );
}

export default function Dashboard() {
  return (
    <DashboardProvider>
      <div className="h-screen w-screen flex flex-col overflow-hidden">
        <Header />
        <div className="flex-1 grid grid-cols-[360px_1fr] grid-rows-3 gap-px bg-border min-h-0">
          {/* Row 1: Scanner | Chart */}
          <div className="bg-surface overflow-hidden row-span-1">
            <MarketScanner />
          </div>
          <div className="bg-surface overflow-hidden row-span-1">
            <PriceChart />
          </div>
          {/* Row 2: Signals | Risk+Portfolio */}
          <div className="bg-surface overflow-hidden row-span-1">
            <SignalDetails />
          </div>
          <div className="bg-surface overflow-hidden row-span-1">
            <TabbedPanel />
          </div>
          {/* Row 3: Blotter | Feed */}
          <div className="bg-surface overflow-hidden row-span-1">
            <TradeBlotter />
          </div>
          <div className="bg-surface overflow-hidden row-span-1">
            <LiveFeed />
          </div>
        </div>
      </div>
    </DashboardProvider>
  );
}
