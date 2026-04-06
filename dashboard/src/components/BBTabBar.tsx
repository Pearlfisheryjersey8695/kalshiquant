"use client";

type TabId = "intel" | "scanner" | "analytics" | "execute" | "risk" | "review";

const TABS: { id: TabId; fkey: string; label: string }[] = [
  { id: "intel", fkey: "F1", label: "INTEL" },
  { id: "scanner", fkey: "F2", label: "SCANNER" },
  { id: "analytics", fkey: "F3", label: "ANALYTICS" },
  { id: "execute", fkey: "F4", label: "EXECUTE" },
  { id: "risk", fkey: "F5", label: "RISK" },
  { id: "review", fkey: "F6", label: "REVIEW" },
];

interface Props {
  activeTab: TabId;
  onTabChange: (tab: TabId) => void;
}

export type { TabId };

export default function BBTabBar({ activeTab, onTabChange }: Props) {
  return (
    <div className="h-[34px] bg-[#080808] border-b border-bb-border flex items-center px-2 gap-0 shrink-0">
      {TABS.map((tab) => {
        const active = activeTab === tab.id;
        return (
          <button
            key={tab.id}
            onClick={() => onTabChange(tab.id)}
            className={`h-full px-3 text-[13px] tracking-wide border-b-2 ${
              active
                ? "text-bb-orange border-bb-orange"
                : "text-bb-dim border-transparent hover:text-bb-white"
            }`}
            style={{ background: "transparent" }}
          >
            <span className="text-bb-dim text-[11px]">{tab.fkey} </span>
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
