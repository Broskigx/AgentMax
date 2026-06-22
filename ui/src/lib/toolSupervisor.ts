import type { BetaTool, ToolRisk } from './toolRegistry';

export interface ToolDecision {
  allowed: boolean;
  blocked: boolean;
  requiresApproval: boolean;
  reason: string;
  riskLevel: ToolRisk;
  flags: string[];
}

const destructivePatterns = [
  /\brm\s+-rf\b/i,
  /\bdel\s+\/s\b/i,
  /\bformat\b/i,
  /\bshutdown\b/i,
  /\btaskkill\b/i,
  /\bstop-process\b/i,
  /\bremove-item\b.*\b(-recurse|-force)\b/i,
  /(iwr|irm|curl|wget).*(iex|invoke-expression|powershell|cmd)/i,
];

const readOnlyPatterns = [
  /^\s*(dir|ls|whoami|tasklist|systeminfo)\b/i,
  /^\s*get-(process|childitem|wmiobject|ciminstance|winevent)\b/i,
  /^\s*wevtutil\s+qe\b/i,
];

const highRiskPatterns = [
  /\b(npm|pnpm|yarn|pip|uv|cargo)\s+(install|add|remove|uninstall)\b/i,
  /\b(reg\s+(add|delete)|set-itemproperty)\b/i,
  /\b(copy|move|ren|rename|set-content|out-file)\b/i,
];

export function inspectCommand(command: string, approved = false): ToolDecision {
  const clean = command.trim();
  if (!clean) {
    return { allowed: false, blocked: true, requiresApproval: false, reason: 'Comando vacio.', riskLevel: 'low', flags: ['empty_command'] };
  }
  const destructive = destructivePatterns.find((pattern) => pattern.test(clean));
  if (destructive) {
    return {
      allowed: false,
      blocked: true,
      requiresApproval: true,
      reason: `Bloqueado por patron peligroso: ${destructive.source}`,
      riskLevel: 'critical',
      flags: ['dangerous_command'],
    };
  }
  if (readOnlyPatterns.some((pattern) => pattern.test(clean))) {
    return { allowed: true, blocked: false, requiresApproval: false, reason: 'Comando de observacion permitido.', riskLevel: 'low', flags: ['read_only'] };
  }
  if (highRiskPatterns.some((pattern) => pattern.test(clean))) {
    return {
      allowed: approved,
      blocked: false,
      requiresApproval: !approved,
      reason: 'El comando modifica entorno o archivos; requiere confirmacion.',
      riskLevel: 'high',
      flags: ['approval_required'],
    };
  }
  return {
    allowed: approved,
    blocked: false,
    requiresApproval: !approved,
    reason: 'No esta clasificado como lectura; requiere confirmacion.',
    riskLevel: 'medium',
    flags: ['approval_required'],
  };
}

export function decideTool(tool: BetaTool, input: Record<string, unknown> = {}, approved = false): ToolDecision {
  // cmd-category tools (CMD/PowerShell, read-only and write) carry a shell
  // command string; inspect it so destructive commands are gated regardless of
  // the tool's display name.
  if (tool.category === 'cmd') {
    return inspectCommand(String(input.command || ''), approved);
  }
  if (tool.unavailableReason) {
    return { allowed: false, blocked: true, requiresApproval: false, reason: tool.unavailableReason, riskLevel: tool.riskLevel, flags: ['unavailable'] };
  }
  if (tool.readOnly) {
    return { allowed: true, blocked: false, requiresApproval: false, reason: 'Tool read-only autorizada.', riskLevel: tool.riskLevel, flags: ['read_only'] };
  }
  if (tool.requiresApproval && !approved) {
    return { allowed: false, blocked: false, requiresApproval: true, reason: 'Requiere confirmacion humana.', riskLevel: tool.riskLevel, flags: ['approval_required'] };
  }
  return { allowed: true, blocked: false, requiresApproval: false, reason: 'Tool aprobada.', riskLevel: tool.riskLevel, flags: [] };
}
