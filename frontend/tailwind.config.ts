import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#eef7ff",
          500: "#1f7ae0",
          600: "#1663ba",
          700: "#0f4d94",
        },
      },
    },
  },
  plugins: [],
} satisfies Config;
