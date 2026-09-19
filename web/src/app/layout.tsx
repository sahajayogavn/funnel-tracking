import type { Metadata } from "next";
import "./globals.css";
import Link from "next/link";
import { cookies } from "next/headers";
import { SESSION_COOKIE, validSession } from "@/lib/auth";

export const metadata: Metadata = {
  title: "Funnel Tracking — Sahaja Yoga Vietnam",
  description: "Seeker CRM & Customer Journey Analytics for Thiền Sahaja Yoga Việt Nam",
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const authenticated = validSession((await cookies()).get(SESSION_COOKIE)?.value);
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet" />
      </head>
      <body>
        {authenticated && <aside className="sidebar">
          <div className="sidebar-header">
            <div className="sidebar-brand">🪷 Sahaja Yoga VN</div>
            <div className="sidebar-subtitle">Funnel Tracking</div>
          </div>
          <nav className="sidebar-nav">
            <Link href="/seekers" className="nav-link">
              <span className="nav-icon">👥</span>
              Seekers
            </Link>
            <Link href="/stats" className="nav-link">
              <span className="nav-icon">📊</span>
              Stats
            </Link>
            <Link href="/graph" className="nav-link">
              <span className="nav-icon">🕸️</span>
              Network Graph
            </Link>
            <Link href="/queues" className="nav-link">
              <span className="nav-icon">✅</span>
              Queue MAS
            </Link>
            <Link href="/llm" className="nav-link">
              <span className="nav-icon">🔬</span>
              Debugging LLM/MAS
            </Link>
          </nav>
        </aside>}
        <main className={authenticated ? "main-content" : undefined}>
          {children}
        </main>
      </body>
    </html>
  );
}
