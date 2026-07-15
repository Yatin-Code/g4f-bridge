import type { ModelEntry, BackendKey } from './types.js';

const BRIDGE_PORT = 1337;
const BRIDGE_BASE = `http://127.0.0.1:${BRIDGE_PORT}`;

export async function healthCheck(): Promise<boolean> {
  try {
    const res = await fetch(`${BRIDGE_BASE}/v1/models`, {
      signal: AbortSignal.timeout(2000),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function fetchBridgeModels(): Promise<ModelEntry[]> {
  const res = await fetch(`${BRIDGE_BASE}/v1/models`);
  if (!res.ok) return [];
  const data = await res.json() as { data: ModelEntry[] };
  return data.data ?? [];
}

export async function fetchG4FModels(url: string, key: string): Promise<ModelEntry[]> {
  try {
    const res = await fetch(`${url}/models`, {
      headers: { Authorization: `Bearer ${key}` },
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) return [];
    const data = await res.json() as { data: Array<{ id: string; label?: string; model?: string; requests?: number }> };
    return (data.data ?? [])
      .filter(m => m.id !== 'auto')
      .map(m => ({
        id: m.id,
        label: m.label ?? m.id,
        model: m.model ?? '',
        requests: m.requests ?? 0,
        backend: 'G4F' as BackendKey,
      }));
  } catch {
    return [];
  }
}

export async function fetchEAONCatalog(url: string, key: string): Promise<ModelEntry[]> {
  try {
    const res = await fetch(`${url}/models/catalog`, {
      headers: { Authorization: `Bearer ${key}` },
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) return [];
    const data = await res.json() as { data: Array<{ id: string; tier?: string }> };
    return (data.data ?? []).map(m => ({
      id: m.id,
      label: `EAON:${m.id}`,
      model: m.id,
      requests: 0,
      backend: 'EAON' as BackendKey,
      tier: m.tier,
    }));
  } catch {
    return [];
  }
}

export async function fetchEAONMonitor(url: string, key: string): Promise<Set<string>> {
  try {
    const res = await fetch(`${url}/monitor/models`, {
      headers: { Authorization: `Bearer ${key}` },
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) return new Set();
    const data = await res.json() as { data: Array<{ id: string; status: string }> };
    return new Set(
      (data.data ?? []).filter(m => m.status === 'operational').map(m => m.id),
    );
  } catch {
    return new Set();
  }
}

export async function testModelLive(
  backend: BackendKey,
  backendUrl: string,
  apiKey: string,
  modelId: string,
  operationalModels?: Set<string>,
): Promise<{ passed: boolean; latency: number; detail: string }> {
  const start = Date.now();
  try {
    const largeContext = 'This is a dummy context string to test large context windows. '.repeat(1500);
    const payload = {
      model: modelId,
      messages: [
        { role: 'system', content: `You are a test agent. ${largeContext}` },
        { role: 'user', content: 'Call the test_tool function right now to confirm tool support, then stop.' },
      ],
      tools: [{
        type: 'function',
        function: {
          name: 'test_tool',
          description: 'A test tool to verify compatibility.',
          parameters: { type: 'object', properties: {} },
        },
      }],
      stream: true,
    };
    const res = await fetch(`${backendUrl}/chat/completions`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(25_000),
    });
    const latency = Date.now() - start;
    if (!res.ok) {
      const text = await res.text();
      return { passed: false, latency, detail: `HTTP ${res.status}: ${text.slice(0, 100)}` };
    }
    const reader = res.body?.getReader();
    if (!reader) return { passed: false, latency, detail: 'No response body' };
    const decoder = new TextDecoder();
    let sawContent = false;
    let sawToolCall = false;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunk = decoder.decode(value, { stream: true });
      const lines = chunk.split('\n');
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6).trim();
        if (data === '[DONE]') break;
        try {
          const parsed = JSON.parse(data);
          const delta = parsed.choices?.[0]?.delta ?? {};
          if (delta.content) sawContent = true;
          if (delta.tool_calls) {
            sawToolCall = true;
            break;
          }
        } catch {
          // skip unparseable chunks
        }
      }
      if (sawToolCall) break;
    }
    if (sawToolCall) {
      return { passed: true, latency, detail: 'Tool call confirmed — supports function calling' };
    }
    if (sawContent) {
      return { passed: false, latency, detail: 'Model streamed text but never called the tool' };
    }
    return { passed: false, latency, detail: 'Empty stream received' };
  } catch (err) {
    const latency = Date.now() - start;
    const msg = err instanceof Error ? err.message : String(err);
    return { passed: false, latency, detail: msg };
  }
}
