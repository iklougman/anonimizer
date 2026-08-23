import "./globals.css";
import type { Metadata } from "next";
import { ColorSchemeScript, MantineProvider, mantineHtmlProps } from "@mantine/core";
import { Inter } from "next/font/google";
import { theme } from "@/lib/theme";
import { SessionProviderWrapper } from "@/components/SessionProviderWrapper";
import { MeProvider } from "@/components/MeProvider";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "Aigenta",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={inter.variable} {...mantineHtmlProps}>
      <head>
        <ColorSchemeScript defaultColorScheme="light" />
      </head>
      <body>
        <MantineProvider theme={theme} defaultColorScheme="light">
          <SessionProviderWrapper>
            <MeProvider>{children}</MeProvider>
          </SessionProviderWrapper>
        </MantineProvider>
      </body>
    </html>
  );
}
