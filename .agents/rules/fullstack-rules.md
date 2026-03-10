---
trigger: always_on
glob: "web/**/*.{ts,tsx}"
description: Full-Stack Web Development Rules
---

# Full-Stack Web Development Rules

These rules govern the creation and modification of the Next.js web application in `web/`.

1. **Server/Client Boundary**: Never import `better-sqlite3` or `db.ts` from client components. Use `types.ts` for type imports and API routes for data fetching.
2. **Server Components by Default**: All pages are Server Components. Only add `'use client'` when React hooks, event handlers, or browser APIs are needed.
3. **Dynamic Rendering**: All pages that access the SQLite database MUST export `dynamic = 'force-dynamic'` to prevent build-time prerendering failures.
4. **API Design**: API routes go in `src/app/api/`. Use `NextResponse.json()` for responses. Always wrap in try/catch.
5. **Component Naming**: kebab-case filenames (e.g., `seekers-table.tsx`). PascalCase exports (e.g., `SeekersTable`).
6. **Tailwind CSS**: Use Tailwind utilities where possible. Custom CSS goes in `globals.css` for design system tokens.
7. **Universal IDs**: Tag significant components with `// code:web-<entity>-NNN:<component>` in comments.
8. **Journey Engine**: All seeker journey logic lives in `src/lib/journey-engine.ts`. Stage transitions define trigger, condition, and action.
9. **Database Access**: Read-only from `memory/agent_memory/frankensqlite.db` via `better-sqlite3`. Python tools handle write operations.
10. **Graph Visualization**: Use `react-force-graph-2d` for WebGL-accelerated network graphs. Custom canvas rendering for node visuals.
