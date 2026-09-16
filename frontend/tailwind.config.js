/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          blue: "#0F9BAA",
          light: "#F4F7F7",
          darkblue: "#0B5563",
          charcoal: "#1F2A2D",
          primary: "#0F9BAA",
          "primary-hover": "#0B7E87",
          dark: "#0B5563",
          surface: "#E6F4F5",
          success: "#16A34A",
          warning: "#D97706",
          error: "#DC2626",
        },
        font: {
          dark: "#1F2A2D",
          blue: "#0B7E87",
        },
      },
    },
  },
  plugins: [],
};
