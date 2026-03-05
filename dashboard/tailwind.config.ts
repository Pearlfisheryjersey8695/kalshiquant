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
        bg: "#0a0a0f",
        surface: "#12121a",
        border: "#1e1e2e",
        "text-primary": "#e0e0e0",
        "text-secondary": "#888899",
        green: "#00d26a",
        red: "#ff3b3b",
        blue: "#3b82f6",
        amber: "#f59e0b",
      },
      fontFamily: {
        mono: ["JetBrains Mono", "monospace"],
        sans: ["IBM Plex Sans", "sans-serif"],
      },
    },
  },
  plugins: [],
};
export default config;
