import type { CSSProperties } from "react";

// Ported directly from the aigenta-frontend design reference's shared brand
// package (/Users/imac/Documents/PROD/aigenta-brand/src/AigentaLogo.tsx) --
// a self-contained styled wordmark (no image asset), not a Mantine component,
// so it's copied here as a plain local component rather than installed as a
// new package dependency.

export type AigentaLogoVariant = "dark" | "light" | "color";

const VARIANT_COLOR: Record<AigentaLogoVariant, string> = {
  dark: "#1971c2", // blue.700 -- default, for light backgrounds
  light: "#ffffff", // for dark/colored backgrounds
  color: "#38B2AC", // brand teal
};

const SIZE_MAP = {
  xs: "0.875rem",
  sm: "1.125rem",
  md: "1.5rem",
  lg: "2.25rem",
  xl: "3rem",
  "2xl": "4rem",
} as const;

export interface AigentaLogoProps {
  text?: string;
  size?: keyof typeof SIZE_MAP;
  variant?: AigentaLogoVariant;
  className?: string;
  style?: CSSProperties;
}

export function AigentaLogo({ text, size = "md", variant = "dark", className, style }: AigentaLogoProps) {
  const resolvedText = (text ?? "AIGENTA").toUpperCase();

  const baseStyle: CSSProperties = {
    fontFamily: "var(--font-inter), -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    fontWeight: 900,
    letterSpacing: "-1px",
    lineHeight: 1,
    textTransform: "uppercase",
    fontSize: SIZE_MAP[size],
    color: VARIANT_COLOR[variant],
    display: "inline-block",
    userSelect: "none",
    ...style,
  };

  return (
    <span className={className} style={baseStyle} aria-label={resolvedText} role="img">
      {resolvedText}
    </span>
  );
}
