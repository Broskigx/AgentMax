export interface BetaTool {
  name: string;
  description: string;
  category: 'screen' | 'cmd' | 'file' | 'automation' | 'browser' | 'system';
  readOnly: boolean;
  requiresApproval: boolean;
  riskLevel: 'low' | 'medium' | 'high' | 'critical';
  requiresServer: boolean;
  unavailableReason?: string;
}

export type ToolRisk = BetaTool['riskLevel'];

export const BETA_TOOL_REGISTRY: BetaTool[] = [
  {
    name: 'Screenshot',
    description: 'Capture screen image',
    category: 'screen',
    readOnly: true,
    requiresApproval: false,
    riskLevel: 'low',
    requiresServer: true,
  },
  {
    name: 'CMD (read-only)',
    description: 'Run cmd.exe commands (read-only safe commands)',
    category: 'cmd',
    readOnly: true,
    requiresApproval: false,
    riskLevel: 'low',
    requiresServer: true,
  },
  {
    name: 'PowerShell (read-only)',
    description: 'Run PowerShell commands (read-only safe commands)',
    category: 'cmd',
    readOnly: true,
    requiresApproval: false,
    riskLevel: 'low',
    requiresServer: true,
  },
  {
    name: 'Read File',
    description: 'Read a file from disk',
    category: 'file',
    readOnly: true,
    requiresApproval: false,
    riskLevel: 'low',
    requiresServer: true,
  },
  {
    name: 'Write File',
    description: 'Write/save a file to disk',
    category: 'file',
    readOnly: false,
    requiresApproval: true,
    riskLevel: 'medium',
    requiresServer: true,
  },
  {
    name: 'List Directory',
    description: 'List files in a directory',
    category: 'file',
    readOnly: true,
    requiresApproval: false,
    riskLevel: 'low',
    requiresServer: true,
  },
  {
    name: 'Search Files',
    description: 'Search files by pattern',
    category: 'file',
    readOnly: true,
    requiresApproval: false,
    riskLevel: 'low',
    requiresServer: true,
  },
  {
    name: 'CMD (write)',
    description: 'Run cmd.exe commands with system changes',
    category: 'cmd',
    readOnly: false,
    requiresApproval: true,
    riskLevel: 'high',
    requiresServer: true,
  },
  {
    name: 'PowerShell (write)',
    description: 'Run PowerShell commands with system changes',
    category: 'cmd',
    readOnly: false,
    requiresApproval: true,
    riskLevel: 'high',
    requiresServer: true,
  },
  {
    name: 'Kill Process',
    description: 'Terminate a running process',
    category: 'system',
    readOnly: false,
    requiresApproval: true,
    riskLevel: 'critical',
    requiresServer: true,
  },
  {
    name: 'Mouse Move',
    description: 'Move mouse cursor',
    category: 'automation',
    readOnly: false,
    requiresApproval: true,
    riskLevel: 'medium',
    requiresServer: true,
  },
  {
    name: 'Mouse Click',
    description: 'Click at coordinates',
    category: 'automation',
    readOnly: false,
    requiresApproval: true,
    riskLevel: 'high',
    requiresServer: true,
  },
  {
    name: 'Type Text',
    description: 'Type text into focused window',
    category: 'automation',
    readOnly: false,
    requiresApproval: true,
    riskLevel: 'high',
    requiresServer: true,
  },
  {
    name: 'OCR',
    description: 'Extract text from screen via OCR',
    category: 'screen',
    readOnly: true,
    requiresApproval: false,
    riskLevel: 'low',
    requiresServer: true,
  },
  {
    name: 'Web Search',
    description: 'Search the web via browser',
    category: 'browser',
    readOnly: true,
    requiresApproval: false,
    riskLevel: 'low',
    requiresServer: true,
  },
  {
    name: 'Stop Task',
    description: 'Cancel the current agent task',
    category: 'system',
    readOnly: true,
    requiresApproval: false,
    riskLevel: 'low',
    requiresServer: false,
  },
];

export function toolAvailability(
  tool: BetaTool,
  serverReady: boolean
): { available: boolean; reason?: string } {
  if (tool.requiresServer && !serverReady) {
    return { available: false, reason: 'Requires AgentMax server (port 7790)' };
  }
  return { available: true };
}
