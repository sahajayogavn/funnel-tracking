import { NextRequest, NextResponse } from "next/server";
import { execute } from "@/lib/db";

type DeleteRequest = {
  traceId?: unknown;
  clearAll?: unknown;
};

// LLM traces are operational logs. Deleting a trace removes every call that
// belongs to that run, so a multi-agent run cannot be left half-visible.
export async function DELETE(request: NextRequest) {
  const body = (await request.json().catch(() => ({}))) as DeleteRequest;
  if (body.clearAll === true) {
    const result = await execute("DELETE FROM llm_calls");
    return NextResponse.json({ deleted: result.changes, cleared: true });
  }

  const traceId = typeof body.traceId === "string" ? body.traceId.trim() : "";
  if (!traceId) {
    return NextResponse.json(
      { error: "Provide a trace ID or request a full log clear." },
      { status: 400 },
    );
  }

  const result = await execute("DELETE FROM llm_calls WHERE trace_id = ?", [traceId]);
  if (!result.changes) {
    return NextResponse.json({ error: "Trace was not found." }, { status: 404 });
  }

  return NextResponse.json({ traceId, deleted: result.changes });
}
