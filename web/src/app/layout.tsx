import type { Metadata } from "next";
import "./globals.css";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Funnel Tracking — Sahaja Yoga Vietnam",
  description: "Seeker CRM & Customer Journey Analytics for Thiền Sahaja Yoga Việt Nam",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet" />
      </head>
      <body>
        <aside className="sidebar">
          <div className="sidebar-header">
            <div className="sidebar-brand">🪷 Sahaja Yoga VN</div>
            <div className="sidebar-subtitle">Funnel Tracking</div>
          </div>
          <nav className="sidebar-nav">
            <Link href="/" className="nav-link">
              <span className="nav-icon">📊</span>
              Dashboard
            </Link>
            <Link href="/seekers" className="nav-link">
              <span className="nav-icon">👥</span>
              Seekers
            </Link>
            <Link href="/graph" className="nav-link">
              <span className="nav-icon">🕸️</span>
              Network Graph
            </Link>
            <Link href="/journey" className="nav-link">
              <span className="nav-icon">🛤️</span>
              Journey Workflow
            </Link>
          </nav>
        </aside>
        <main className="main-content">
          {children}
        </main>
      </body>
    </html>
  );
}
