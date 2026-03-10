# System Architecture — Funnel Tracking Platform

**Universal ID**: `doc:architecture-001`

## Overview

The platform combines **Python agent tools** (data fetching) with a **Next.js web dashboard** (data visualization & CRM), sharing a single **FrankenSQLite** database.

```
┌─────────────────────────────────────────────────────────┐
│  Data Collection Layer (Python 3.13 CLI Tools)          │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐│
│  │fetch_comments │ │fetch_messages│ │telegram_notify   ││
│  └──────┬───────┘ └──────┬───────┘ └──────────────────┘│
│         │                │                              │
│         ▼                ▼                              │
│  ┌──────────────────────────────────────────┐           │
│  │   FrankenSQLite (memory/agent_memory/)   │           │
│  │   threads│messages│users│posts│comments  │           │
│  └──────────────────┬───────────────────────┘           │
│                     │ (readonly)                        │
│         ┌───────────┴───────────┐                       │
│  ┌──────▼──────┐ ┌──────────────▼─────────────────┐    │
│  │ API Routes  │ │ Server Components (SSR)         │    │
│  │ /api/seekers│ │ Dashboard, Seekers, Journey     │    │
│  │ /api/graph  │ │                                 │    │
│  │ /api/stats  │ │                                 │    │
│  └──────┬──────┘ └──────────────┬─────────────────┘    │
│         │                       │                       │
│  ┌──────▼───────────────────────▼─────────────────┐    │
│  │  Client Components (React, Canvas, WebGL)       │    │
│  │  ┌──────────┐ ┌────────────┐ ┌────────────────┐│    │
│  │  │Seekers   │ │Network     │ │Journey Flow    ││    │
│  │  │Table     │ │Graph (2D)  │ │(React Flow)    ││    │
│  │  └──────────┘ └────────────┘ └────────────────┘│    │
│  └────────────────────────────────────────────────┘    │
│                Next.js 16 (web/)                        │
└─────────────────────────────────────────────────────────┘
```

## Data Model

```
Page (page_id = 1548373332058326)
├── Post (post_id) ← ads, video, image, text
│   ├── Comment (by UserID) ← public touch-point
│   │   └── Reply (by PageID) ← our response
│   └── comment_users CRM table
├── Thread (thread_id) ← private DM touch-point
│   ├── Message (by UserID or PageID)
│   └── users CRM table
└── Unified User Identity (fb_user_id + fb_profile_url)
    └── Customer Journey: Unknown → Seeker → ... → Sahaja Mahayogi
```

## Seeker Journey Stages

| #   | Stage                 | Description                          |
| --- | --------------------- | ------------------------------------ |
| 0   | Unknown               | Not yet identified                   |
| 1   | Seeker                | First interaction with Page          |
| 2   | Public Program Seeker | Attending public meditation programs |
| 3   | 18-Week Seeker        | Enrolled in deep learning course     |
| 4   | Seed                  | Foundation of Sahaja Yoga            |
| 5   | Sahaja Yogi           | Regular practitioner                 |
| 6   | Dedicated Sahaja Yogi | Fully dedicated                      |
| 7   | Sahaja Mahayogi       | Highest spiritual dedication         |

## Tech Stack

| Layer               | Technology                            |
| ------------------- | ------------------------------------- |
| Data Collection     | Python 3.13, Playwright, argparse     |
| Database            | SQLite (FrankenSQLite)                |
| Backend             | Next.js 16 API Routes, better-sqlite3 |
| Frontend            | React 19, Tailwind CSS, Canvas2D      |
| Graph Visualization | react-force-graph-2d (WebGL)          |
| Journey Workflow    | @xyflow/react (React Flow)            |
| Skills              | 7 agent skills (symlinked)            |

## Web App Routes

| Route          | Type        | Description                        |
| -------------- | ----------- | ---------------------------------- |
| `/`            | Dynamic SSR | Dashboard with stats               |
| `/seekers`     | Dynamic SSR | CRM table with histograms          |
| `/graph`       | Static      | WebGL network graph                |
| `/journey`     | Dynamic SSR | React Flow AI workflow             |
| `/api/seekers` | API         | Seeker data, activity, touchpoints |
| `/api/graph`   | API         | Graph data for visualization       |
| `/api/stats`   | API         | Dashboard statistics               |

## Cities

The 7 target cities for Sahaja Yoga Vietnam:

1. Hà Nội
2. Bắc Ninh
3. Hải Phòng
4. Hưng Yên
5. Nghệ An
6. Đà Nẵng
7. Tp. Hồ Chí Minh
