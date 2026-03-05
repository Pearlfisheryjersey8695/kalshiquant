"use client";

interface PanelHeaderProps {
  title: string;
  subtitle?: string;
  right?: React.ReactNode;
}

export default function PanelHeader({ title, subtitle, right }: PanelHeaderProps) {
  return (
    <div className="flex items-center justify-between px-3 py-1.5 border-b border-border shrink-0">
      <div className="flex items-center gap-2">
        <span className="text-xs font-semibold text-text-secondary uppercase tracking-wider">
          {title}
        </span>
        {subtitle && (
          <span className="text-[10px] text-text-secondary">{subtitle}</span>
        )}
      </div>
      {right && <div className="flex items-center gap-2">{right}</div>}
    </div>
  );
}
