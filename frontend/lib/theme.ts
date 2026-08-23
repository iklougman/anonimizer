import { createTheme, type MantineColorsTuple } from "@mantine/core";

// Every palette below is copied verbatim from the aigenta-frontend design
// reference (/Users/imac/Documents/PROD/aigenta-frontend/src/lib/theme.ts) --
// the full color set, not just the brand teal/sage.
const blue: MantineColorsTuple = [
  "#e7f5ff",
  "#d0ebff",
  "#a5d8ff",
  "#74c0fc",
  "#339af0",
  "#228be6",
  "#1c7ed6",
  "#1971c2",
  "#1864ab",
  "#0c5aa6",
];

const green: MantineColorsTuple = [
  "#f0fff4",
  "#dcfce7",
  "#bbf7d0",
  "#86efac",
  "#4ade80",
  "#22c55e",
  "#16a34a",
  "#15803d",
  "#166534",
  "#14532d",
];

const orange: MantineColorsTuple = [
  "#fff7ed",
  "#ffedd5",
  "#fed7aa",
  "#fdba74",
  "#fb923c",
  "#f97316",
  "#ea580c",
  "#dc2626",
  "#b91c1c",
  "#991b1b",
];

const purple: MantineColorsTuple = [
  "#faf5ff",
  "#f3e8ff",
  "#e9d5ff",
  "#d8b4fe",
  "#c084fc",
  "#a855f7",
  "#9333ea",
  "#7c3aed",
  "#6d28d9",
  "#5b21b6",
];

const red: MantineColorsTuple = [
  "#fef2f2",
  "#fee2e2",
  "#fecaca",
  "#fca5a5",
  "#f87171",
  "#ef4444",
  "#dc2626",
  "#b91c1c",
  "#991b1b",
  "#7f1d1d",
];

const teal: MantineColorsTuple = [
  "#E6FFFA",
  "#B2F5EA",
  "#81E6D9",
  "#4FD1C5",
  "#38B2AC", // Primary
  "#319795",
  "#2C7A7B",
  "#285E61",
  "#234E52",
  "#1D4044",
];

const sage: MantineColorsTuple = [
  "#F0F4F2", // Lightest sage
  "#E1E8E4",
  "#C6D6CE",
  "#ABC3B7",
  "#91B1A1", // Base sage
  "#7A9E8D",
  "#648B79",
  "#4F7666",
  "#3D6154",
  "#2B4C42",
];

// The reference switches between two whole `createTheme()` objects at
// runtime via its own custom ThemeContext. This app instead expresses both
// color schemes in one theme (Mantine's built-in per-scheme resolution via
// `primaryShade` + a custom `dark` neutral tuple), so MantineProvider handles
// light/dark switching itself -- no custom context needed. Copied from the
// reference's own `dark` tuple.
const dark: MantineColorsTuple = [
  "#C1C2C5",
  "#A6A7AB",
  "#909296",
  "#5c5f66",
  "#373A40",
  "#2C2E33",
  "#25262b",
  "#1A1B1E",
  "#141517",
  "#101113",
];

export const theme = createTheme({
  primaryColor: "teal",
  primaryShade: { light: 4, dark: 6 },

  colors: { teal, sage, blue, green, orange, purple, red, dark },

  fontFamily: "var(--font-inter), -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
  fontFamilyMonospace: "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",

  headings: {
    fontFamily: "var(--font-inter), -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
    fontWeight: "600",
  },

  fontSizes: {
    xs: "0.75rem",
    sm: "0.875rem",
    md: "1rem",
    lg: "1.125rem",
    xl: "1.25rem",
  },

  radius: {
    xs: "0.25rem",
    sm: "0.375rem",
    md: "0.5rem",
    lg: "0.75rem",
    xl: "1rem",
  },

  spacing: {
    xs: "0.5rem",
    sm: "0.75rem",
    md: "1rem",
    lg: "1.5rem",
    xl: "2rem",
  },

  shadows: {
    xs: "0 1px 2px 0 rgb(0 0 0 / 0.05)",
    sm: "0 1px 3px 0 rgb(0 0 0 / 0.1), 0 1px 2px -1px rgb(0 0 0 / 0.1)",
    md: "0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)",
    lg: "0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1)",
    xl: "0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1)",
  },

  components: {
    Card: { defaultProps: { padding: "md", radius: "md", shadow: "xs", withBorder: true } },
    Paper: { defaultProps: { radius: "md" } },
    Badge: { defaultProps: { radius: "sm", variant: "light" } },
    Button: { defaultProps: { radius: "sm" } },
    Table: { defaultProps: { highlightOnHover: true, verticalSpacing: "sm", horizontalSpacing: "md" } },
    Modal: { defaultProps: { radius: "md" } },
    Tabs: { defaultProps: { radius: "sm" } },
    TextInput: { defaultProps: { radius: "sm" } },
    Select: { defaultProps: { radius: "sm" } },
    PasswordInput: { defaultProps: { radius: "sm" } },
  },
});
