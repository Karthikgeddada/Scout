import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          '"SF Pro Text"',
          '"SF Pro Display"',
          '"Helvetica Neue"',
          '"Segoe UI"',
          "Roboto",
          "Arial",
          "sans-serif",
        ],
      },
      colors: {
        brand: {
          50: "#f0f7ff",
          100: "#dcecff",
          200: "#b9d8ff",
          300: "#8abdff",
          400: "#54a1ff",
          500: "#0071e3", // Apple blue
          600: "#0058b9",
          700: "#00448f",
        },
        canvas: "#f5f5f7", // Apple light-grey canvas
      },
      boxShadow: {
        soft: "0 2px 12px rgba(0, 0, 0, 0.06)",
        lift: "0 8px 24px rgba(0, 0, 0, 0.08)",
      },
    },
  },
  plugins: [],
} satisfies Config;
