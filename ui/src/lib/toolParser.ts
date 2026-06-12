export interface ParsedTool {
  name: string;
  params: Record<string, string>;
  confidence: number;
}

/**
 * Extract tool calls from AI-generated content.
 *
 * Recognizes formats:
 * - ```tool:name ... ``` blocks
 * - [TOOL:name] ... [/TOOL] tags
 * - XML-style <tool name="name">...</tool>
 * - Inline JSON { "tool": "...", "params": {...} }
 */
export function extractTools(content: string): { clean: string; tools: ParsedTool[] } {
  if (!content) return { clean: '', tools: [] };

  let clean = content;
  const tools: ParsedTool[] = [];

  // Extract fenced tool blocks: ```tool:name
  const fencedRegex = /```(?:tool)?:?(\w+)\s*\n([\s\S]*?)```/g;
  let match: RegExpExecArray | null;
  while ((match = fencedRegex.exec(content)) !== null) {
    const name = match[1].toUpperCase();
    const rawParams = match[2].trim();
    const params = parseParams(rawParams);
    tools.push({ name, params, confidence: 0.9 });
    clean = clean.replace(match[0], '').trim();
  }

  // Extract [TOOL:name] blocks
  const tagRegex = /\[TOOL:(\w+)\]([\s\S]*?)\[\/TOOL\]/g;
  while ((match = tagRegex.exec(content)) !== null) {
    const name = match[1].toUpperCase();
    const rawParams = match[2].trim();
    const params = parseParams(rawParams);
    tools.push({ name, params, confidence: 0.85 });
    clean = clean.replace(match[0], '').trim();
  }

  // Extract XML-style tool tags
  const xmlRegex = /<tool\s+name=["'](\w+)["'][^>]*>([\s\S]*?)<\/tool>/g;
  while ((match = xmlRegex.exec(content)) !== null) {
    const name = match[1].toUpperCase();
    const rawParams = match[2].trim();
    const params = parseParams(rawParams);
    tools.push({ name, params, confidence: 0.8 });
    clean = clean.replace(match[0], '').trim();
  }

  // Extract inline JSON tool definitions
  const jsonRegex = /\{\s*"tool"\s*:\s*"(\w+)"\s*,\s*"params"\s*:\s*\{([\s\S]*?)\}\s*\}/g;
  while ((match = jsonRegex.exec(content)) !== null) {
    try {
      const parsed = JSON.parse(match[0]);
      tools.push({
        name: parsed.tool.toUpperCase(),
        params: parsed.params || {},
        confidence: 0.75,
      });
      clean = clean.replace(match[0], '').trim();
    } catch {
      // Skip invalid JSON
    }
  }

  return { clean: clean.trim(), tools };
}

function parseParams(raw: string): Record<string, string> {
  const params: Record<string, string> = {};

  // Try JSON first
  try {
    const parsed = JSON.parse(raw);
    if (typeof parsed === 'object' && !Array.isArray(parsed)) {
      for (const [key, value] of Object.entries(parsed)) {
        params[key] = String(value);
      }
      return params;
    }
  } catch {
    // Not JSON, try key=value pairs
  }

  // Parse key=value pairs (one per line)
  const lines = raw.split('\n');
  for (const line of lines) {
    const eqMatch = line.match(/^\s*(\w+)\s*=\s*(.+?)\s*$/);
    if (eqMatch) {
      params[eqMatch[1]] = eqMatch[2].replace(/^["']|["']$/g, '');
      continue;
    }
    // If there's only one value without a key, use it as positional
    const trimmed = line.trim();
    if (trimmed && Object.keys(params).length === 0) {
      params['0'] = trimmed;
    }
  }

  // If no structured params found, use entire raw as value
  if (Object.keys(params).length === 0 && raw) {
    params['0'] = raw;
  }

  return params;
}
