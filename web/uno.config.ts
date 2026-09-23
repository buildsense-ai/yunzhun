import { defineConfig, presetUno, presetIcons } from "unocss";

// Bio-lab dark theme: deep chlorophyll blacks, emerald/teal accents,
// amber for waiting states, rose for failures.
export default defineConfig({
  presets: [presetUno(), presetIcons()],
  theme: {
    colors: {
      bio: {
        bg: "#070d0b",        // deep green-black, like a dark lab
        panel: "#0d1512",     // card surface
        edge: "#1c2b25",      // borders
        ink: "#d7e5de",       // primary text (faded mint)
        dim: "#6b857c",       // secondary text
        emerald: "#34d399",   // success / downloaded
        teal: "#2dd4bf",      // identified
        amber: "#fbbf24",     // pending
        rose: "#fb7185",      // failed
        slate: "#64748b",     // other mail
        lime: "#a3e635",      // accent pop
      },
    },
    fontFamily: {
      mono: ["'JetBrains Mono'", "ui-monospace", "monospace"],
      sans: ["'Noto Sans SC'", "system-ui", "sans-serif"],
    },
  },
  shortcuts: {
    "bio-card": "bg-bio-panel border border-bio-edge rounded-lg",
    "bio-chip": "px-1.5 py-0.5 rounded text-[10px] font-mono border",
  },
});
