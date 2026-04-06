import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // Bloomberg Terminal palette
        "bb-black": "#000000",
        "bb-panel": "#0a0a0a",
        "bb-border": "#1a1a1a",
        "bb-header": "#0f0f0f",
        "bb-orange": "#ff6600",
        "bb-green": "#00ff00",
        "bb-red": "#ff0000",
        "bb-yellow": "#ffff00",
        "bb-blue": "#00aaff",
        "bb-white": "#ffffff",
        "bb-dim": "#888888",
        "bb-row-even": "#080808",
        "bb-selected": "#1a1a00",
        // Legacy aliases (for gradual migration)
        bg: "#000000",
        surface: "#0a0a0a",
        border: "#1a1a1a",
        "text-primary": "#ffffff",
        "text-secondary": "#888888",
        green: { DEFAULT: "#00ff00" },
        red: { DEFAULT: "#ff0000" },
        blue: { DEFAULT: "#00aaff" },
        amber: { DEFAULT: "#ff6600" },
      },
      fontFamily: {
        mono: ["'IBM Plex Mono'", "'Courier New'", "monospace"],
      },
      fontSize: {
        "bb-xs": "9px",
        "bb-sm": "10px",
        "bb-base": "11px",
        "bb-md": "12px",
        "bb-lg": "13px",
      },
    },
  },
  plugins: [],
};
export default config;
