import "./globals.css";
import { SessionProviderWrapper } from "@/components/SessionProviderWrapper";
import { MeProvider } from "@/components/MeProvider";

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <SessionProviderWrapper>
          <MeProvider>{children}</MeProvider>
        </SessionProviderWrapper>
      </body>
    </html>
  );
}
