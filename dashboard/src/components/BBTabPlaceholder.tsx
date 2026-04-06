"use client";

interface Props {
  title: string;
  fkey: string;
  description: string;
}

export default function BBTabPlaceholder({ title, fkey, description }: Props) {
  return (
    <div className="h-full flex flex-col items-center justify-center bg-bb-black">
      <div className="text-bb-orange text-[13px] font-medium tracking-wider">{fkey} {title}</div>
      <div className="text-bb-dim text-[11px] mt-2">{description}</div>
      <div className="text-bb-dim text-[10px] mt-4 border border-bb-border px-4 py-2">
        UNDER CONSTRUCTION
      </div>
    </div>
  );
}
