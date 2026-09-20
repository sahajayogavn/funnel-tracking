"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { LlmCall } from "@/lib/types";
import {
  getLlmRouteInfo,
  getLlmStatusLabel,
  getLlmStatusTone,
} from "@/lib/llm-labels";
import { MessengerMessageList } from "@/components/messenger-message-list";

type Tab = "Context" | "Prompt" | "State" | "Tools" | "Response" | "Outcome";
type Trace = { traceId: string; calls: LlmCall[] };
type RouteBreakdown = {
  route: string;
  calls: number;
  errors: number;
  attention: number;
  duration: number;
};
type CustomerMessage = { text: string; sender?: string; timestamp?: string };

type LlmClientProps = {
  traces: Trace[];
  stats: {
    totalCalls: number;
    p50: number;
    p95: number;
    tokensIn: number;
    tokensOut: number;
    percentAttention: number;
    percentError: number;
    verifyVsDetect: number;
  };
  routeBreakdown: RouteBreakdown[];
  filters: Record<string, string>;
};

function parseJson(value: string | null): unknown {
  if (!value) return null;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

type ToolEvent = {
  phase?: string;
  tool_name?: string;
  arguments?: unknown;
  result?: unknown;
  error?: string;
  tool?: unknown;
};

type ToolOutput = {
  fields?: Array<{ label: string; value: string; status?: boolean }>;
  raw?: string;
  text?: string;
  tone: "error" | "ok";
};

type AgentInvocation = {
  /**
   * A presentation-only grouping. `calls` always retains the unmodified,
   * individually selectable audit rows from llm_calls.
   */
  calls: LlmCall[];
  toolEvents: ToolEvent[];
};

function extractToolEvents(call: LlmCall): ToolEvent[] {
  const response = parseJson(call.response_json) as Record<
    string,
    unknown
  > | null;
  return response && Array.isArray(response.tool_calls)
    ? response.tool_calls.filter(
        (event): event is ToolEvent =>
          Boolean(event) && typeof event === "object",
      )
    : [];
}

function toolEventName(event: ToolEvent): string {
  if (event.tool_name) return event.tool_name;
  if (event.tool && typeof event.tool === "object") {
    const tool = event.tool as Record<string, unknown>;
    return String(tool.name || tool.function_name || "Tool call");
  }
  return "Tool call";
}

function hasExecutedTool(call: LlmCall): boolean {
  const phases = new Set(
    extractToolEvents(call)
      .map((event) => String(event.phase || "").toLowerCase())
      .filter(Boolean),
  );
  // A requested function call by itself is not enough: grouping it with a
  // later same-agent call could hide a retry. A recorded start/result proves
  // this was the tool-turn continuation of the preceding model call.
  return phases.has("requested") && (phases.has("started") || phases.has("completed"));
}

function countToolExecutions(events: ToolEvent[]): number {
  type Execution = { id: number; started: boolean };
  const activeBySignature = new Map<string, Execution[]>();
  const ids = new Set<number>();
  let nextId = 0;

  events.forEach((event) => {
    const embedded = event.tool && typeof event.tool === "object"
      ? event.tool as Record<string, unknown>
      : null;
    const args = event.arguments ?? (embedded && embedded.args);
    const signature = `${toolEventName(event)}:${JSON.stringify(args || {})}`;
    const active = activeBySignature.get(signature) || [];
    const phase = String(event.phase || "requested").toLowerCase();

    if (phase === "requested") {
      const execution = { id: ++nextId, started: false };
      active.push(execution);
      ids.add(execution.id);
    } else {
      let execution = phase === "started"
        ? active.find((item) => !item.started)
        : active[0];
      if (!execution) {
        execution = { id: ++nextId, started: false };
        active.push(execution);
        ids.add(execution.id);
      }
      execution.started = true;
      if (phase === "completed" || phase === "error") active.shift();
    }
    activeBySignature.set(signature, active);
  });
  return ids.size;
}

/**
 * Groups only a direct, adjacent model continuation after a recorded tool
 * execution. The database's parent_call_id is a general causal chain, so it
 * alone must not merge retries or separate turns by the same agent.
 */
export function groupTraceAgentInvocations(calls: LlmCall[]): AgentInvocation[] {
  const ordered = [...calls].sort(
    (left, right) => left.seq_in_trace - right.seq_in_trace || left.id - right.id,
  );
  const groups: AgentInvocation[] = [];

  for (const call of ordered) {
    const prior = groups.at(-1);
    const previousCall = prior?.calls.at(-1);
    const isDirectToolContinuation = Boolean(
      prior &&
        previousCall &&
        call.trace_id === previousCall.trace_id &&
        call.agent_name &&
        call.agent_name === previousCall.agent_name &&
        call.parent_call_id === previousCall.id &&
        call.seq_in_trace === previousCall.seq_in_trace + 1 &&
        call.attempt === previousCall.attempt &&
        hasExecutedTool(previousCall),
    );

    if (isDirectToolContinuation && prior && previousCall) {
      prior.calls.push(call);
      prior.toolEvents.push(...extractToolEvents(previousCall));
    } else {
      groups.push({ calls: [call], toolEvents: [] });
    }
  }
  return groups;
}

function toolFieldLabel(key: string): string {
  const labels: Record<string, string> = {
    chars: "Characters",
    city: "City",
    count: "Count",
    status: "Status",
  };
  return labels[key] || key.replace(/[_-]+/g, " ");
}

function formatToolFieldValue(value: unknown): string {
  if (value === null) return "—";
  if (typeof value === "number") return new Intl.NumberFormat().format(value);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return `${value.length} items`;
  if (value && typeof value === "object")
    return `${Object.keys(value).length} fields`;
  return String(value);
}

function toolEventOutput(event: ToolEvent): ToolOutput | null {
  if (event.error) return { text: event.error, tone: "error" };
  if (!("result" in event) || event.result === undefined) return null;
  if (typeof event.result === "string")
    return { text: event.result, tone: "ok" };
  if (event.result && typeof event.result === "object" && !Array.isArray(event.result)) {
    const result = event.result as Record<string, unknown>;
    return {
      fields: Object.entries(result).map(([key, value]) => ({
        label: toolFieldLabel(key),
        value: formatToolFieldValue(value),
        status: key === "status",
      })),
      raw: JSON.stringify(event.result, null, 2),
      tone: "ok",
    };
  }
  return { text: formatToolFieldValue(event.result), tone: "ok" };
}

function messageText(value: unknown): string | null {
  if (typeof value === "string") return value.trim() || null;
  if (Array.isArray(value))
    return value.map(messageText).filter(Boolean).join("\n") || null;
  if (value && typeof value === "object") {
    const item = value as Record<string, unknown>;
    for (const key of ["text", "content", "message", "value", "body"]) {
      const text = messageText(item[key]);
      if (text) return text;
    }
  }
  return null;
}

function isCustomerMessage(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  const role = String(
    item.role || item.sender || item.author || item.direction || "",
  ).toLowerCase();
  return ["user", "customer", "seeker", "inbound", "human"].some((label) =>
    role.includes(label),
  );
}

function isCustomerSender(sender: string): boolean {
  return ["user", "customer", "seeker", "inbound", "human"].some((label) =>
    sender.toLowerCase().includes(label),
  );
}

function parseThreadTranscript(thread: string): CustomerMessage[] {
  const messages: CustomerMessage[] = [];
  let current: CustomerMessage | null = null;

  for (const line of thread.split("\n")) {
    const match = line.match(/^\[([^\]|]+)\s*\|\s*([^\]]+)]\s*(.*)$/);
    if (match) {
      if (current && current.text.trim())
        messages.push({ ...current, text: current.text.trim() });
      current = {
        timestamp: match[1].trim(),
        sender: match[2].trim(),
        text: match[3],
      };
    } else if (current) {
      current.text += `${current.text ? "\n" : ""}${line}`;
    }
  }
  if (current && current.text.trim())
    messages.push({ ...current, text: current.text.trim() });

  return messages;
}

function extractConversationMessages(call: LlmCall): CustomerMessage[] {
  const state = parseJson(call.state_json) as Record<string, unknown> | null;
  if (state) {
    for (const key of [
      "customer_message",
      "latest_customer_message",
      "inbound_message",
      "reaction_content",
    ]) {
      const text = messageText(state[key]);
      if (text) return [{ text }];
    }
    const thread = state.thread_messages;
    if (typeof thread === "string") {
      const transcriptMessages = parseThreadTranscript(thread);
      if (transcriptMessages.length) return transcriptMessages;
      if (thread.trim()) return [{ text: thread.trim() }];
    }
    if (Array.isArray(thread)) {
      const customerMessages = thread
        .filter(isCustomerMessage)
        .map(messageText)
        .filter((text): text is string => Boolean(text));
      const fallback = thread
        .map(messageText)
        .filter((text): text is string => Boolean(text));
      if (customerMessages.length)
        return customerMessages.map((text) => ({ text }));
      if (fallback.length) return fallback.map((text) => ({ text }));
    }

    const batchPayload = state.batch_payload;
    if (typeof batchPayload === "string") {
      const parsedBatch = parseJson(batchPayload);
      if (Array.isArray(parsedBatch)) {
        const messages = parsedBatch.flatMap((item) => {
          if (!item || typeof item !== "object") return [];
          const value = item as Record<string, unknown>;
          return Array.isArray(value.messages) ? value.messages : [];
        });
        const customerMessages = messages
          .filter(isCustomerMessage)
          .map(messageText)
          .filter((text): text is string => Boolean(text));
        const fallback = messages
          .map(messageText)
          .filter((text): text is string => Boolean(text));
        if (customerMessages.length)
          return customerMessages.map((text) => ({ text }));
        if (fallback.length) return fallback.map((text) => ({ text }));
      }
      if (batchPayload.trim()) return [{ text: batchPayload.slice(0, 1200) }];
    }
  }
  const messagesValue = parseJson(call.messages_json) as unknown;
  const messages = Array.isArray(messagesValue)
    ? messagesValue
    : messagesValue &&
        typeof messagesValue === "object" &&
        Array.isArray((messagesValue as Record<string, unknown>).messages)
      ? ((messagesValue as Record<string, unknown>).messages as unknown[])
      : messagesValue &&
          typeof messagesValue === "object" &&
          Array.isArray((messagesValue as Record<string, unknown>).contents)
        ? ((messagesValue as Record<string, unknown>).contents as unknown[])
        : [];
  const userMessages = messages.filter(
    (item) =>
      isCustomerMessage(item) ||
      (!!item &&
        typeof item === "object" &&
        String((item as Record<string, unknown>).role || "").toLowerCase() ===
          "user"),
  );
  const customerMessages = userMessages
    .map(messageText)
    .filter((text): text is string => Boolean(text));
  if (customerMessages.length)
    return customerMessages.map((text) => ({ text }));
  const fallback = messageText(messages[messages.length - 1]);
  return fallback ? [{ text: fallback }] : [];
}

function formatMs(value: number | null | undefined): string {
  if (!value) return "—";
  if (value < 1000) return `${Math.round(value)}ms`;
  return `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}s`;
}

function formatTokens(value: number): string {
  if (!value) return "0";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`;
  return String(value);
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function prettyJson(value: string | null): string {
  if (!value) return "No data recorded.";
  const parsed = parseJson(value);
  return parsed === null ? value : JSON.stringify(parsed, null, 2);
}

function getTraceTone(calls: LlmCall[]): string {
  if (calls.some((call) => getLlmStatusTone(call.status) === "error"))
    return "status-error";
  if (calls.some((call) => getLlmStatusTone(call.status) === "attention"))
    return "status-attention";
  if (calls.some((call) => getLlmStatusTone(call.status) === "running"))
    return "status-running";
  return "status-ok";
}

function traceDuration(trace: Trace): number {
  return trace.calls.reduce(
    (total, call) => total + (call.duration_ms || 0),
    0,
  );
}
function traceTokenUsage(trace: Trace): { input: number; output: number } {
  return trace.calls.reduce(
    (usage, call) => ({
      input: usage.input + (call.tokens_in || 0),
      output: usage.output + (call.tokens_out || 0),
    }),
    { input: 0, output: 0 },
  );
}
function traceKind(trace: Trace): string {
  return trace.calls.some((call) => call.route_group === "MAS")
    ? "MAS action"
    : "LLM run";
}

export function generateReproCommand(call: LlmCall): string {
  return `OPENAI_API_KEY=YOUR_API_KEY OPENAI_API_BASE=YOUR_API_BASE .venv/bin/python tools/repro_llm.py --route ${call.route || "unknown_route"} --subject ${call.subject_id || ""} --trace ${call.trace_id}`;
}

export default function LlmObservabilityClient({
  traces,
  stats,
  routeBreakdown,
  filters,
}: LlmClientProps) {
  const router = useRouter();
  const initialTrace = filters.trace
    ? traces.find((trace) => trace.traceId === filters.trace)
    : traces[0];
  const [expandedTraces, setExpandedTraces] = useState<Set<string>>(() =>
    initialTrace ? new Set([initialTrace.traceId]) : new Set(),
  );
  const [selectedCallId, setSelectedCallId] = useState<number | null>(
    () => initialTrace?.calls[0]?.id ?? null,
  );
  const [activeTab, setActiveTab] = useState<Tab>("Context");
  const [toast, setToast] = useState<string | null>(null);
  const [showRouteBreakdown, setShowRouteBreakdown] = useState(false);
  const [conversationExpanded, setConversationExpanded] = useState(false);
  const [draftFilters, setDraftFilters] = useState(filters);
  const [deletingLog, setDeletingLog] = useState<string | null>(null);

  useEffect(() => {
    if (!toast) return;
    const timeout = window.setTimeout(() => setToast(null), 2200);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const updateDraft = (key: string, value: string) =>
    setDraftFilters((current) => ({ ...current, [key]: value }));
  const applyFilters = (event?: React.FormEvent) => {
    event?.preventDefault();
    const params = new URLSearchParams();
    Object.entries(draftFilters).forEach(([key, value]) => {
      if (value && key !== "trace")
        params.set(key === "search" ? "q" : key, value);
    });
    router.push(`/llm${params.toString() ? `?${params.toString()}` : ""}`);
  };
  const setDatePreset = (days: number) => {
    const end = new Date();
    const start = new Date();
    start.setDate(end.getDate() - days + 1);
    const toDate = (date: Date) => date.toISOString().slice(0, 10);
    const next = {
      ...draftFilters,
      from: toDate(start),
      to: toDate(end),
      date: "",
    };
    setDraftFilters(next);
    router.push(`/llm?from=${next.from}&to=${next.to}`);
  };
  const clearFilters = () => {
    setDraftFilters({});
    router.push("/llm");
  };
  const copyText = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text || "");
      setToast(`${label} copied`);
    } catch {
      setToast("Clipboard unavailable");
    }
  };
  const selectCall = (call: LlmCall, traceId: string) => {
    setSelectedCallId(call.id);
    setExpandedTraces((current) => new Set(current).add(traceId));
    setActiveTab("Context");
    setConversationExpanded(false);
  };
  const deleteLog = async (options: { traceId?: string; clearAll?: boolean }) => {
    const isClearAll = options.clearAll === true;
    if (
      isClearAll &&
      !window.confirm("Permanently delete every LLM log entry? This cannot be undone.")
    )
      return;

    const deletionKey = isClearAll ? "all" : options.traceId || null;
    setDeletingLog(deletionKey);
    try {
      const response = await fetch("/api/llm", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(options),
      });
      const result = (await response.json()) as { deleted?: number; error?: string };
      if (!response.ok) throw new Error(result.error || "Could not delete the log.");
      setToast(
        isClearAll
          ? `Cleared ${result.deleted || 0} LLM log entries`
          : `Deleted ${result.deleted || 0} calls from this run`,
      );
      router.refresh();
    } catch (error) {
      setToast(error instanceof Error ? error.message : "Could not delete the log.");
    } finally {
      setDeletingLog(null);
    }
  };

  const selectedCall =
    traces
      .flatMap((trace) => trace.calls)
      .find((call) => call.id === selectedCallId) ||
    traces[0]?.calls[0] ||
    null;
  const selectedRoute = selectedCall
    ? getLlmRouteInfo(selectedCall.route, selectedCall.trigger)
    : null;
  const selectedConversationMessages = selectedCall
    ? extractConversationMessages(selectedCall)
    : [];
  const selectedToolEvents = selectedCall
    ? extractToolEvents(selectedCall)
    : [];
  const activeTrace = selectedCall
    ? traces.find((trace) =>
        trace.calls.some((call) => call.id === selectedCall.id),
      )
    : null;
  const hasFilters = Object.entries(filters).some(
    ([key, value]) => Boolean(value) && key !== "trace",
  );
  const statCards = [
    {
      label: "LLM calls",
      value: String(stats.totalCalls),
      hint: "in current view",
      tone: "accent-indigo",
    },
    {
      label: "Latency",
      value: `${formatMs(stats.p50)} / ${formatMs(stats.p95)}`,
      hint: "p50 / p95",
      tone: "accent-cyan",
    },
    {
      label: "Tokens",
      value: `${formatTokens(stats.tokensIn)} in · ${formatTokens(stats.tokensOut)} out`,
      hint: "recorded usage",
      tone: "accent-purple",
    },
    {
      label: "Needs attention",
      value: `${stats.percentAttention.toFixed(1)}%`,
      hint: `${stats.percentError.toFixed(1)}% errors`,
      tone: stats.percentError > 0 ? "accent-rose" : "accent-amber",
    },
  ];

  return (
    <div
      className="llm-observability"
      data-ds-id="ds:screen:llm-observability-001"
    >
      <header className="llm-page-header">
        <div>
          <div className="llm-eyebrow">
            <span className="llm-live-dot" /> LLM operations · Phase 3
          </div>
          <h1>LLM observability</h1>
          <p>
            Review what the MAS saw, decided, and handed to a human before it
            reaches a seeker.
          </p>
        </div>
        <div className="llm-header-actions">
          <button
            type="button"
            className="llm-clear-log-action"
            onClick={() => void deleteLog({ clearAll: true })}
            disabled={deletingLog !== null || !traces.length}
            title="Permanently clear all LLM log entries"
          >
            <span aria-hidden="true">⌫</span> Clear LLM Log
          </button>
          <Link href="/queues" className="llm-secondary-action">
            Open approval queues <span>↗</span>
          </Link>
          <Link href="/seekers" className="llm-secondary-action">
            Browse seekers <span>↗</span>
          </Link>
        </div>
      </header>

      <section className="llm-stat-grid" aria-label="LLM health summary">
        {statCards.map((stat) => (
          <div className="llm-stat-card" key={stat.label}>
            <div className="llm-stat-topline">
              <span className={`llm-stat-icon ${stat.tone}`} />
              {stat.label}
            </div>
            <div className="llm-stat-value">{stat.value}</div>
            <div className="llm-stat-hint">{stat.hint}</div>
          </div>
        ))}
      </section>

      <form className="llm-filter-panel" onSubmit={applyFilters}>
        <div className="llm-filter-heading">
          <div>
            <strong>Find a run</strong>
            <span>Filter traces by workflow, trigger, or seeker context.</span>
          </div>
          {hasFilters && (
            <button
              type="button"
              className="llm-clear-button"
              onClick={clearFilters}
            >
              Clear filters
            </button>
          )}
        </div>
        <div className="llm-presets" aria-label="Date presets">
          <button
            type="button"
            className={
              !filters.from && !filters.to && !filters.date ? "active" : ""
            }
            onClick={() => {
              setDraftFilters({ ...draftFilters, from: "", to: "", date: "" });
              router.push("/llm");
            }}
          >
            Today
          </button>
          <button type="button" onClick={() => setDatePreset(7)}>
            Last 7 days
          </button>
          <button type="button" onClick={() => setDatePreset(30)}>
            Last 30 days
          </button>
          <label>
            From{" "}
            <input
              type="date"
              value={draftFilters.from || draftFilters.date || ""}
              onChange={(event) => updateDraft("from", event.target.value)}
            />
          </label>
          <label>
            To{" "}
            <input
              type="date"
              value={draftFilters.to || ""}
              onChange={(event) => updateDraft("to", event.target.value)}
            />
          </label>
        </div>
        <div className="llm-filter-grid">
          <label>
            Group
            <select
              value={draftFilters.group || ""}
              onChange={(event) => updateDraft("group", event.target.value)}
            >
              <option value="">All groups</option>
              <option value="MAS">MAS workflows</option>
              <option value="LLM">Direct LLM</option>
            </select>
          </label>
          <label>
            Trigger
            <select
              value={draftFilters.trigger || ""}
              onChange={(event) => updateDraft("trigger", event.target.value)}
            >
              <option value="">All triggers</option>
              <option value="scheduler">Scheduler</option>
              <option value="web">Web / queues</option>
              <option value="cli">CLI</option>
              <option value="hitl_regen">HITL regenerate</option>
            </select>
          </label>
          <label>
            Status
            <select
              value={draftFilters.status || ""}
              onChange={(event) => updateDraft("status", event.target.value)}
            >
              <option value="">All statuses</option>
              <option value="skipped">Không đề xuất</option>
              <option value="ok">OK</option>
              <option value="empty">Empty</option>
              <option value="sanitized_empty">Sanitized empty</option>
              <option value="error">Error</option>
              <option value="timeout">Timeout</option>
            </select>
          </label>
          <label>
            Route
            <input
              placeholder="e.g. warmup"
              value={draftFilters.route || ""}
              onChange={(event) => updateDraft("route", event.target.value)}
            />
          </label>
          <label>
            Agent
            <input
              placeholder="e.g. Responder"
              value={draftFilters.agent || ""}
              onChange={(event) => updateDraft("agent", event.target.value)}
            />
          </label>
          <label className="llm-filter-wide">
            Seeker or text
            <input
              placeholder="Name, thread ID, prompt, response…"
              value={draftFilters.subject || ""}
              onChange={(event) => updateDraft("subject", event.target.value)}
            />
          </label>
          <label className="llm-filter-wide">
            Full-text search
            <input
              placeholder="Search prompt and response"
              value={draftFilters.search || ""}
              onChange={(event) => updateDraft("search", event.target.value)}
            />
          </label>
          <button className="llm-apply-button" type="submit">
            Apply filters <span>→</span>
          </button>
        </div>
      </form>

      <section className="llm-breakdown-card">
        <button
          className="llm-breakdown-toggle"
          type="button"
          onClick={() => setShowRouteBreakdown((value) => !value)}
          aria-expanded={showRouteBreakdown}
        >
          <span>
            <strong>Route health</strong>
            <small>
              See which workflow needs attention · Verify / detect{" "}
              {stats.verifyVsDetect.toFixed(2)}
            </small>
          </span>
          <span>{showRouteBreakdown ? "⌃" : "⌄"}</span>
        </button>
        {showRouteBreakdown && (
          <div className="llm-breakdown-table">
            {routeBreakdown.length ? (
              routeBreakdown.map((item) => {
                const route = getLlmRouteInfo(item.route);
                return (
                  <div className="llm-breakdown-row" key={item.route}>
                    <span className={`llm-route-mark ${route.accent}`} />
                    <span className="llm-breakdown-route">
                      <strong>{route.label}</strong>
                      <small>{route.useCase}</small>
                    </span>
                    <span>{item.calls} calls</span>
                    <span>{formatMs(item.duration)} total</span>
                    <span className={item.errors ? "llm-danger-text" : ""}>
                      {item.errors
                        ? `${item.errors} errors`
                        : item.attention
                          ? `${item.attention} attention`
                          : "Healthy"}
                    </span>
                  </div>
                );
              })
            ) : (
              <div className="llm-empty-inline">
                No route data for this filter.
              </div>
            )}
          </div>
        )}
      </section>

      <div className="llm-workspace">
        <section
          className="llm-trace-pane"
          aria-label="MAS actions and LLM runs"
        >
          <div className="llm-section-heading">
            <div>
              <h2>Recent actions</h2>
              <span>
                {traces.length} trace{traces.length === 1 ? "" : "s"} · newest
                first
              </span>
            </div>
            <span className="llm-count-pill">{stats.totalCalls} calls</span>
          </div>
          {traces.length ? (
            traces.map((trace) => {
              const firstCall = trace.calls[0];
              const route = getLlmRouteInfo(
                firstCall?.route,
                firstCall?.trigger,
              );
              const isExpanded = expandedTraces.has(trace.traceId);
              const subject =
                firstCall?.subject_label ||
                firstCall?.subject_id ||
                "No subject attached";
              const okCount = trace.calls.filter(
                (call) => getLlmStatusTone(call.status) === "ok",
              ).length;
              const isRunning = trace.calls.some(
                (call) => getLlmStatusTone(call.status) === "running",
              );
              const tokenUsage = traceTokenUsage(trace);
              return (
                <article
                  className={`llm-trace-card ${getTraceTone(trace.calls)} ${isExpanded ? "is-expanded" : ""}`}
                  key={trace.traceId}
                >
                  <button
                    className="llm-trace-summary"
                    type="button"
                    onClick={() =>
                      setExpandedTraces((current) => {
                        const next = new Set(current);
                        if (next.has(trace.traceId)) next.delete(trace.traceId);
                        else next.add(trace.traceId);
                        return next;
                      })
                    }
                  >
                    <span className="llm-trace-chevron">
                      {isExpanded ? "⌄" : "›"}
                    </span>
                    <span className="llm-trace-main">
                      <span className="llm-trace-meta">
                        <span>
                          {formatDateTime(firstCall?.started_at || "")}
                        </span>
                        <span className="llm-trigger-label">
                          {firstCall?.trigger || "unknown"}
                        </span>
                        <span className="llm-route-label">{route.label}</span>
                      </span>
                      <strong>
                        {isRunning && (
                          <span
                            className="llm-running-indicator"
                            aria-label="LLM is running"
                            title="LLM is running"
                          >
                            <svg viewBox="0 0 16 16" aria-hidden="true">
                              <circle cx="8" cy="8" r="5.5" />
                              <path d="M8 2.5a5.5 5.5 0 0 1 5.5 5.5" />
                              <path d="M8 5.25v2.9l2 1.2" />
                            </svg>
                            <span>LLM running</span>
                          </span>
                        )}
                        <span className="llm-trace-subject">{subject}</span>
                      </strong>
                      <small>
                        {traceKind(trace)} ·{" "}
                        <code>{trace.traceId.slice(0, 12)}…</code> ·{" "}
                        {trace.calls.filter((call) => call.model !== "deterministic").length} calls ·{" "}
                        {trace.calls.filter((call) => call.model === "deterministic").length} decisions ·{" "}
                        {formatMs(traceDuration(trace))} total ·{" "}
                        {formatTokens(tokenUsage.input)} in ·{" "}
                        {formatTokens(tokenUsage.output)} out
                      </small>
                    </span>
                    <span className="llm-trace-result">
                      <span
                        className={`llm-status-badge ${getLlmStatusTone(trace.calls.find((call) => getLlmStatusTone(call.status) !== "ok")?.status || "ok")}`}
                      >
                        {okCount}/{trace.calls.length} OK
                      </span>
                      <span
                        className="llm-trace-copy"
                        onClick={(event) => {
                          event.stopPropagation();
                          copyText(
                            JSON.stringify(trace.calls, null, 2),
                            "Trace JSON",
                          );
                        }}
                      >
                        Copy trace
                      </span>
                    </span>
                  </button>
                  <button
                    type="button"
                    className="llm-trace-delete"
                    onClick={() => void deleteLog({ traceId: trace.traceId })}
                    disabled={deletingLog !== null}
                    aria-label={`Delete LLM run ${trace.traceId}`}
                    title="Delete this LLM run"
                  >
                    <svg viewBox="0 0 16 16" aria-hidden="true">
                      <path d="M3.5 4.5h9M6.25 2.5h3.5l.75 2H5.5l.75-2ZM5 6.5v5.5M8 6.5v5.5M11 6.5v5.5M4.5 4.5l.55 9h5.9l.55-9" />
                    </svg>
                  </button>
                  {isExpanded && (
                    <div className="llm-call-list">
                      {groupTraceAgentInvocations(trace.calls).map((invocation) => {
                        const [firstCall] = invocation.calls;
                        const invocationTokens = invocation.calls.reduce(
                          (usage, call) => ({
                            input: usage.input + (call.tokens_in || 0),
                            output: usage.output + (call.tokens_out || 0),
                            duration: usage.duration + (call.duration_ms || 0),
                          }),
                          { input: 0, output: 0, duration: 0 },
                        );
                        const hasContinuation = invocation.calls.length > 1;
                        return (
                          <section
                            className={`llm-agent-invocation ${hasContinuation ? "is-tool-continuation" : ""}`}
                            key={firstCall.id}
                            aria-label={hasContinuation ? `${firstCall.agent_name || "Agent"} tool-assisted invocation` : undefined}
                          >
                            {hasContinuation && (
                              <div className="llm-agent-invocation-summary">
                                <strong>{firstCall.agent_name || "Agent"}</strong>
                                <span>
                                  {invocation.calls.length} LLM calls · {countToolExecutions(invocation.toolEvents)} tool execution{countToolExecutions(invocation.toolEvents) === 1 ? "" : "s"} · {formatMs(invocationTokens.duration)} · {formatTokens(invocationTokens.input)} in · {formatTokens(invocationTokens.output)} out
                                </span>
                              </div>
                            )}
                            {invocation.calls.map((call, callIndex) => {
                              const callRoute = getLlmRouteInfo(
                                call.route,
                                call.trigger,
                              );
                              const selected = selectedCall?.id === call.id;
                              const isToolTurn = callIndex === 0 && invocation.toolEvents.length > 0;
                              return (
                                <div key={call.id}>
                                  <button
                                    className={`llm-call-row ${selected ? "selected" : ""}`}
                                    type="button"
                                    onClick={() => selectCall(call, trace.traceId)}
                                  >
                                    <span className="llm-call-seq">
                                      {String(call.seq_in_trace).padStart(2, "0")}
                                    </span>
                                    <span
                                      className={`llm-call-dot ${getLlmStatusTone(call.status)}`}
                                    />
                                    <span className="llm-call-copy">
                                      <strong>
                                        {call.agent_name || callRoute.shortLabel}
                                      </strong>
                                      <small>
                                        {call.model === "deterministic" ? "Quyết định điều kiện · Không gọi model · " : ""}
                                        {callRoute.shortLabel} ·{" "}
                                        {call.subject_label ||
                                          call.subject_id ||
                                          "batch"}
                                      </small>
                                    </span>
                                    <span className="llm-call-metrics">
                                      <span
                                        className={`llm-status-badge ${getLlmStatusTone(call.status)}`}
                                      >
                                        {getLlmStatusLabel(call.status)}
                                      </span>
                                      <small>{formatMs(call.duration_ms)}</small>
                                      <small
                                        className="llm-call-token-usage"
                                        title={`${formatTokens(call.tokens_in || 0)} input tokens · ${formatTokens(call.tokens_out || 0)} output tokens`}
                                      >
                                        {formatTokens(call.tokens_in || 0)} in ·{" "}
                                        {formatTokens(call.tokens_out || 0)} out
                                      </small>
                                    </span>
                                  </button>
                                  {isToolTurn && (
                                    <div className="llm-tool-lifecycle" aria-label="Tool execution lifecycle">
                                      {invocation.toolEvents.map((event, index) => (
                                        <span key={`${toolEventName(event)}-${event.phase || "requested"}-${index}`}>
                                          <b>{String(event.phase || "requested").toUpperCase()}</b> · {toolEventName(event)}
                                        </span>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              );
                            })}
                          </section>
                        );
                      })}
                    </div>
                  )}
                </article>
              );
            })
          ) : (
            <div className="llm-empty-state">
              <div className="llm-empty-icon">⌁</div>
              <h3>No LLM runs found</h3>
              <p>
                {hasFilters
                  ? "Try clearing a filter or widening the date range."
                  : "Run a scheduler or a recommendation from Approval Queues to create an auditable trace."}
              </p>
              {hasFilters && (
                <button type="button" onClick={clearFilters}>
                  Clear filters
                </button>
              )}
            </div>
          )}
        </section>

        <aside className="llm-detail-pane" aria-label="Selected call details">
          {selectedCall && selectedRoute ? (
            <>
              <div className="llm-detail-header">
                <div>
                  <span className={`llm-route-kicker ${selectedRoute.accent}`}>
                    {selectedRoute.group} · {selectedRoute.shortLabel}
                  </span>
                  <h2>{selectedCall.agent_name || "LLM call"}</h2>
                  <p>{selectedRoute.useCase}</p>
                </div>
                <span
                  className={`llm-status-badge ${getLlmStatusTone(selectedCall.status)}`}
                >
                  {getLlmStatusLabel(selectedCall.status)}
                </span>
              </div>
              <div className="llm-detail-context">
                <div>
                  <span>Subject</span>
                  <strong>
                    {selectedCall.subject_label ||
                      selectedCall.subject_id ||
                      "Not attached"}
                  </strong>
                </div>
                <div>
                  <span>Trigger</span>
                  <strong>{selectedCall.trigger || "unknown"}</strong>
                </div>
                <div>
                  <span>Latency</span>
                  <strong>{formatMs(selectedCall.duration_ms)}</strong>
                </div>
                <div>
                  <span>Tokens</span>
                  <strong>
                    {formatTokens(
                      (selectedCall.tokens_in || 0) +
                        (selectedCall.tokens_out || 0),
                    )}
                  </strong>
                </div>
              </div>
              <div className="llm-detail-links">
                {selectedCall.subject_id &&
                  (selectedCall.subject_type === "thread" ||
                    selectedCall.subject_type === "user") && (
                    <Link href={`/seekers/${selectedCall.subject_id}`}>
                      View seeker context <span>↗</span>
                    </Link>
                  )}
                {activeTrace && (
                  <button
                    type="button"
                    onClick={() =>
                      copyText(
                        JSON.stringify(activeTrace.calls, null, 2),
                        "Trace JSON",
                      )
                    }
                  >
                    Copy trace JSON
                  </button>
                )}
              </div>
              <div className="llm-tabs" role="tablist">
                {(
                  ["Context", "Prompt", "State", "Tools", "Response", "Outcome"] as Tab[]
                ).map((tab) => (
                  <button
                    key={tab}
                    type="button"
                    role="tab"
                    aria-selected={activeTab === tab}
                    className={activeTab === tab ? "active" : ""}
                    onClick={() => setActiveTab(tab)}
                  >
                    {tab}{tab === "Tools" && selectedToolEvents.length ? ` (${selectedToolEvents.length})` : ""}
                  </button>
                ))}
              </div>
              <div className="llm-detail-body">
                {activeTab === "Context" && (
                  <div className="llm-context-view">
                    <div className="llm-context-intro">
                      <span className="llm-context-icon">✦</span>
                      <div>
                        <strong>Why did this call run?</strong>
                        <p>
                          {selectedRoute.useCase}. The operator can use the
                          input and output below to decide whether the result is
                          safe to use.
                        </p>
                      </div>
                    </div>
                    <section className="llm-message-card llm-conversation-card">
                      <button
                        type="button"
                        className="llm-conversation-toggle"
                        onClick={() =>
                          setConversationExpanded((expanded) => !expanded)
                        }
                        aria-expanded={conversationExpanded}
                      >
                        <span className="llm-card-label">
                          Model input messages ·{" "}
                          {selectedConversationMessages.length}
                        </span>
                        <span>
                          {conversationExpanded
                            ? "Hide history ⌃"
                            : "Show history ⌄"}
                        </span>
                      </button>
                      {conversationExpanded &&
                        (selectedConversationMessages.length ? (
                          <MessengerMessageList
                            compact
                            maxHeight={360}
                            messages={selectedConversationMessages.map((message, index) => ({
                              id: `${message.timestamp || "message"}-${message.sender || ""}-${index}`,
                              sender: message.sender || "Message",
                              content: message.text,
                              timestamp: message.timestamp,
                              outgoing: Boolean(message.sender && !isCustomerSender(message.sender)),
                            }))}
                          />
                        ) : (
                          <p>
                            No model input message could be extracted from the
                            recorded trace.
                          </p>
                        ))}
                    </section>
                    <div className="llm-message-card llm-message-card--output">
                      <span className="llm-card-label">Model output</span>
                      <p>
                        {selectedCall.sanitized_text ||
                          selectedCall.response_text ||
                          (selectedToolEvents.length
                            ? "Model requested tool execution; inspect the Tools tab."
                            : "No response text recorded.")}
                      </p>
                    </div>
                    {selectedToolEvents.some((event) => Boolean(toolEventOutput(event))) && (
                      <section
                        className="llm-tool-output-card"
                        aria-label="Tool calling output"
                      >
                        <span className="llm-card-label">Tool Calling Output</span>
                        {selectedToolEvents.map((event, index) => {
                          const output = toolEventOutput(event);
                          if (!output) return null;
                          return (
                            <div
                              className={`llm-tool-output-item ${output.tone}`}
                              key={`${toolEventName(event)}-${index}`}
                            >
                              <div className="llm-tool-output-heading">
                                <span className="llm-tool-output-icon" aria-hidden="true">
                                  {output.tone === "error" ? "!" : "✓"}
                                </span>
                                <div>
                                  <strong>{toolEventName(event)}</strong>
                                  <small>
                                    {output.tone === "error"
                                      ? "Tool execution failed"
                                      : "Tool result received"}
                                  </small>
                                </div>
                              </div>
                              {output.fields ? (
                                <div className="llm-tool-output-fields">
                                  {output.fields.map((field) => (
                                    <div key={field.label}>
                                      <span>{field.label}</span>
                                      <strong className={field.status ? "llm-tool-output-status" : ""}>
                                        {field.value}
                                      </strong>
                                    </div>
                                  ))}
                                </div>
                              ) : (
                                <p>{output.text}</p>
                              )}
                              {output.raw && (
                                <details className="llm-tool-output-raw">
                                  <summary>View raw result</summary>
                                  <pre>{output.raw}</pre>
                                </details>
                              )}
                            </div>
                          );
                        })}
                      </section>
                    )}
                    {selectedCall.error && (
                      <div className="llm-error-box">
                        <strong>Call error</strong>
                        <p>{selectedCall.error}</p>
                      </div>
                    )}
                  </div>
                )}
                {activeTab === "Prompt" && (
                  <DataSection
                    title="Rendered system prompt"
                    value={selectedCall.system_prompt}
                    onCopy={() =>
                      copyText(selectedCall.system_prompt || "", "Prompt")
                    }
                  >
                    <div className="llm-subsection-label">Messages</div>
                    <pre>{prettyJson(selectedCall.messages_json)}</pre>
                  </DataSection>
                )}
                {activeTab === "State" && (
                  <DataSection
                    title="Recorded state / batch payload"
                    value={selectedCall.state_json}
                    onCopy={() =>
                      copyText(prettyJson(selectedCall.state_json), "State")
                    }
                  />
                )}
                {activeTab === "Tools" && (
                  <div className="llm-data-stack">
                    {selectedToolEvents.length ? (
                      selectedToolEvents.map((event, index) => (
                        <DataSection
                          key={`${toolEventName(event)}-${index}`}
                          title={`${String(event.phase || "requested").toUpperCase()} · ${toolEventName(event)}`}
                          value={JSON.stringify(event, null, 2)}
                        />
                      ))
                    ) : (
                      <div className="llm-empty-inline">
                        This model turn did not invoke a tool.
                      </div>
                    )}
                  </div>
                )}
                {activeTab === "Response" && (
                  <div className="llm-data-stack">
                    <DataSection
                      title="Raw response"
                      value={selectedCall.response_text}
                      onCopy={() =>
                        copyText(selectedCall.response_text || "", "Response")
                      }
                    />
                    <DataSection
                      title="Sanitized response"
                      value={selectedCall.sanitized_text}
                    />
                    <DataSection
                      title="Response metadata"
                      value={selectedCall.response_json}
                    />
                  </div>
                )}
                {activeTab === "Outcome" && (
                  <div className="llm-outcome-view">
                    <div className="llm-outcome-row">
                      <span>Outcome type</span>
                      <strong>
                        {selectedCall.outcome_type || "No linked outcome"}
                      </strong>
                    </div>
                    <div className="llm-outcome-row">
                      <span>Outcome reference</span>
                      <code>{selectedCall.outcome_ref || "—"}</code>
                    </div>
                    <div className="llm-outcome-note">
                      <strong>Human control stays in the loop</strong>
                      <p>
                        This page is an audit surface. A draft or recommendation
                        is not evidence that a DM was sent; the yogi must review
                        and send it in Facebook.
                      </p>
                    </div>
                    <button
                      className="llm-copy-json-button"
                      type="button"
                      onClick={() =>
                        copyText(
                          JSON.stringify(selectedCall, null, 2),
                          "Call JSON",
                        )
                      }
                    >
                      Copy call JSON
                    </button>
                  </div>
                )}
              </div>
              <div className="llm-detail-footer">
                <span>
                  Call #{selectedCall.id} ·{" "}
                  {formatDateTime(selectedCall.started_at)}
                </span>
                <div>
                  <button
                    type="button"
                    onClick={() =>
                      copyText(
                        `${selectedCall.system_prompt || ""}\n\n--- messages ---\n${prettyJson(selectedCall.messages_json)}`,
                        "Prompt",
                      )
                    }
                  >
                    Copy prompt
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      copyText(
                        `${selectedCall.response_text || ""}${selectedCall.sanitized_text ? `\n\n--- sanitized ---\n${selectedCall.sanitized_text}` : ""}`,
                        "Response",
                      )
                    }
                  >
                    Copy response
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      copyText(
                        generateReproCommand(selectedCall),
                        "Repro command",
                      )
                    }
                  >
                    Copy repro
                  </button>
                </div>
              </div>
            </>
          ) : (
            <div className="llm-detail-empty">
              <div className="llm-detail-empty-icon">◌</div>
              <h2>Choose a call to inspect</h2>
              <p>
                Start with the latest run on the left. The Context tab connects
                the model call to the seeker and use case before you open the
                raw audit data.
              </p>
            </div>
          )}
        </aside>
      </div>
      {toast && (
        <div className="llm-toast" role="status">
          ✓ {toast}
        </div>
      )}
    </div>
  );
}

function DataSection({
  title,
  value,
  onCopy,
  children,
}: {
  title: string;
  value: string | null;
  onCopy?: () => void;
  children?: React.ReactNode;
}) {
  return (
    <section className="llm-data-section">
      <div className="llm-data-heading">
        <span>{title}</span>
        {onCopy && (
          <button type="button" onClick={onCopy}>
            Copy
          </button>
        )}
      </div>
      <pre>{prettyJson(value)}</pre>
      {children}
    </section>
  );
}
