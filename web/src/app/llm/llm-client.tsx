'use client';

import { LlmCall } from '@/lib/types';
import { useState } from 'react';
import { useRouter } from 'next/navigation';

interface LlmClientProps {
  traces: { traceId: string; calls: LlmCall[] }[];
  stats: any;
  filters: any;
}

export function generateReproCommand(call: LlmCall): string {
  // We must ensure API keys are stripped.
  const route = call.route || 'unknown_route';
  const subjectId = call.subject_id || '';
  
  // Safe mock command that definitely does not contain real keys
  return `OPENAI_API_KEY=YOUR_API_KEY OPENAI_API_BASE=YOUR_API_BASE .venv/bin/python tools/repro_llm.py --route ${route} --subject ${subjectId} --trace ${call.trace_id}`;
}

export default function LlmObservabilityClient({ traces, stats, filters }: LlmClientProps) {
  const router = useRouter();
  const [expandedTraces, setExpandedTraces] = useState<Set<string>>(new Set());
  const [selectedCall, setSelectedCall] = useState<LlmCall | null>(null);
  const [activeTab, setActiveTab] = useState<'Prompt' | 'State' | 'Response' | 'Outcome'>('Prompt');

  const toggleTrace = (traceId: string) => {
    const newSet = new Set(expandedTraces);
    if (newSet.has(traceId)) {
      newSet.delete(traceId);
    } else {
      newSet.add(traceId);
    }
    setExpandedTraces(newSet);
  };

  const updateFilter = (key: string, value: string) => {
    const params = new URLSearchParams(window.location.search);
    if (value) {
      params.set(key, value);
    } else {
      params.delete(key);
    }
    router.push(`/llm?${params.toString()}`);
  };

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text || '');
    alert('Copied!');
  };

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-4 gap-4 mb-4">
        <div className="p-4 border rounded shadow-sm">
          <div className="text-sm text-gray-500">p50 / p95 Latency</div>
          <div className="text-lg font-semibold">{stats.p50}ms / {stats.p95}ms</div>
        </div>
        <div className="p-4 border rounded shadow-sm">
          <div className="text-sm text-gray-500">% Sanitized</div>
          <div className="text-lg font-semibold">{stats.percentSanitized.toFixed(1)}%</div>
        </div>
        <div className="p-4 border rounded shadow-sm">
          <div className="text-sm text-gray-500">% Empty</div>
          <div className="text-lg font-semibold">{stats.percentEmpty.toFixed(1)}%</div>
        </div>
        <div className="p-4 border rounded shadow-sm">
          <div className="text-sm text-gray-500">Verify / Detect</div>
          <div className="text-lg font-semibold">{stats.verifyVsDetect.toFixed(2)}</div>
        </div>
      </div>

      <div className="flex flex-wrap gap-2 mb-4">
        <input className="border p-2 rounded" type="date" value={filters.date || ''} onChange={(e) => updateFilter('date', e.target.value)} />
        <input className="border p-2 rounded" placeholder="Filter by group..." value={filters.group || ''} onChange={(e) => updateFilter('group', e.target.value)} />
        <input className="border p-2 rounded" placeholder="Filter by trigger..." value={filters.trigger || ''} onChange={(e) => updateFilter('trigger', e.target.value)} />
        <input className="border p-2 rounded" placeholder="Filter by subject..." value={filters.subject || ''} onChange={(e) => updateFilter('subject', e.target.value)} />
        <input className="border p-2 rounded" placeholder="Filter by agent..." value={filters.agent || ''} onChange={(e) => updateFilter('agent', e.target.value)} />
        <input className="border p-2 rounded" placeholder="Filter by route..." value={filters.route || ''} onChange={(e) => updateFilter('route', e.target.value)} />
        <input className="border p-2 rounded" placeholder="Filter by status..." value={filters.status || ''} onChange={(e) => updateFilter('status', e.target.value)} />
        <input className="border p-2 rounded" placeholder="Search full text..." value={filters.search || ''} onChange={(e) => updateFilter('search', e.target.value)} />
      </div>

      <div className="flex flex-row gap-6">
        <div className="w-1/2 flex flex-col gap-4">
          {traces.map(trace => (
            <div key={trace.traceId} className="border rounded p-4 shadow-sm">
              <div 
                className="flex justify-between items-center cursor-pointer mb-2" 
                onClick={() => toggleTrace(trace.traceId)}
              >
                <h3 className="font-semibold text-lg">Trace: {trace.traceId.slice(0,8)}... ({trace.calls.length} calls)</h3>
                <span>{expandedTraces.has(trace.traceId) ? '▼' : '▶'}</span>
              </div>
              {expandedTraces.has(trace.traceId) && (
                <div className="flex flex-col gap-2 mt-4">
                  {trace.calls.map(call => (
                    <div 
                      key={call.id} 
                      className={`p-3 border rounded cursor-pointer ${selectedCall?.id === call.id ? 'bg-blue-50 border-blue-300' : 'bg-gray-50 hover:bg-gray-100'}`}
                      onClick={() => setSelectedCall(call)}
                    >
                      <div className="flex justify-between">
                        <span className="font-medium text-blue-700">{call.route}</span>
                        <span className={`px-2 rounded text-sm ${call.status === 'success' ? 'bg-green-100' : call.status === 'error' ? 'bg-red-100' : 'bg-yellow-100'}`}>
                          {call.status}
                        </span>
                      </div>
                      <div className="text-sm text-gray-500 mt-1">
                        Agent: {call.agent_name} | Dur: {call.duration_ms}ms
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="w-1/2 border border-gray-800 rounded p-4 shadow-sm bg-zinc-900 text-gray-200 min-h-[500px]">
          {selectedCall ? (
            <div className="flex flex-col h-full">
              <div className="flex justify-between items-center mb-4">
                <h2 className="text-xl font-semibold text-white">Call Details ({selectedCall.id})</h2>
                <div className="flex gap-2 text-sm">
                  <button onClick={() => handleCopy(selectedCall.system_prompt + '\n' + selectedCall.messages_json)} className="px-3 py-1 bg-blue-600 text-white hover:bg-blue-700 rounded">Copy Prompt</button>
                  <button onClick={() => handleCopy(selectedCall.response_text || selectedCall.response_json || '')} className="px-3 py-1 bg-blue-600 text-white hover:bg-blue-700 rounded">Copy Response</button>
                  <button onClick={() => handleCopy(selectedCall.state_json || '')} className="px-3 py-1 bg-blue-600 text-white hover:bg-blue-700 rounded">Copy State</button>
                  <button onClick={() => handleCopy(JSON.stringify(selectedCall, null, 2))} className="px-3 py-1 bg-blue-600 text-white hover:bg-blue-700 rounded">Copy JSON</button>
                  <button onClick={() => handleCopy(generateReproCommand(selectedCall))} className="px-3 py-1 bg-blue-600 text-white hover:bg-blue-700 rounded">Repro-command</button>
                </div>
              </div>

              <div className="flex border-b border-gray-700 mb-4 gap-2">
                {['Prompt', 'State', 'Response', 'Outcome'].map(tab => (
                  <button
                    key={tab}
                    className={`px-4 py-2 rounded-t-md ${activeTab === tab ? 'bg-zinc-800 border-b-2 border-blue-500 font-semibold text-white' : 'text-gray-400 hover:text-gray-200 hover:bg-zinc-800'}`}
                    onClick={() => setActiveTab(tab as any)}
                  >
                    {tab}
                  </button>
                ))}
              </div>

              <div className="flex-grow overflow-y-auto bg-gray-950 text-gray-300 p-4 rounded whitespace-pre-wrap font-mono text-sm">
                {activeTab === 'Prompt' && (
                  <div>
                    <div className="font-bold text-gray-400 mb-2">System Prompt:</div>
                    {selectedCall.system_prompt}
                    <div className="font-bold text-gray-400 mt-4 mb-2">Messages JSON:</div>
                    {selectedCall.messages_json}
                  </div>
                )}
                {activeTab === 'State' && (
                  <div>
                    {selectedCall.state_json || 'No state available.'}
                  </div>
                )}
                {activeTab === 'Response' && (
                  <div>
                    <div className="font-bold text-gray-400 mb-2">Response Text:</div>
                    {selectedCall.response_text || '(Empty)'}
                    {selectedCall.sanitized_text && (
                      <div className="mt-4">
                        <div className="font-bold text-red-400 mb-2">Sanitized Text:</div>
                        {selectedCall.sanitized_text}
                      </div>
                    )}
                    {selectedCall.error && (
                      <div className="mt-4 text-red-500">
                        <div className="font-bold mb-2">Error:</div>
                        {selectedCall.error}
                      </div>
                    )}
                  </div>
                )}
                {activeTab === 'Outcome' && (
                  <div>
                    <div>Outcome Type: {selectedCall.outcome_type || 'N/A'}</div>
                    <div>Outcome Ref: {selectedCall.outcome_ref || 'N/A'}</div>
                    <div>Tokens In: {selectedCall.tokens_in}</div>
                    <div>Tokens Out: {selectedCall.tokens_out}</div>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="text-gray-400 text-center mt-20">Select a call to view details</div>
          )}
        </div>
      </div>
    </div>
  );
}
