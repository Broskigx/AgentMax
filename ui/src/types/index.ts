export type AgentState = 'idle' | 'working' | 'validating' | 'error' | 'completed';

export type ExperienceMode = 'easy' | 'normal' | 'advanced';

export interface TaskStatus {
  id: string;
  description: string;
  status: 'queued' | 'planning' | 'executing' | 'validating' | 'completed' | 'failed' | 'cancelled';
  stepsCompleted: number;
  stepsTotal: number;
  reasoning: string[];
  error?: string;
  startedAt?: number;
  completedAt?: number;
}

export interface AgentEvent {
  type: string;
  topic: string;
  payload: Record<string, unknown>;
  source: string;
  ts: number;
}

export interface AgentMetrics {
  name: string;
  state: string;
  actions: number;
  errors: number;
  avg_latency_ms: number;
}

export interface SystemStatus {
  status: string;
  timestamp: number;
  agents: Record<string, AgentMetrics>;
  bus_depth: number;
}

export interface ToolCatalogItem {
  id: string;
  name: string;
  description: string;
  category: string;
  version: string;
  enabled: boolean;
  permissions: string[];
  risk_level: 'low' | 'medium' | 'high' | 'critical';
  requires_user_idle: boolean;
  execution_mode: string;
}

export interface ToolDiagnostics {
  catalog?: {
    count: number;
    valid: boolean;
    categories: Record<string, ToolCatalogItem[]>;
    issues?: Array<{ code: string; message: string; field?: string }>;
  };
  runtime?: {
    states?: Record<string, {
      status: string;
      last_error?: string | null;
      run_count: number;
      error_count: number;
    }>;
    history?: Array<{
      success: boolean;
      tool: string;
      error?: string | null;
      duration_ms: number;
      confidence: number;
      status: string;
    }>;
  };
  input_monitor?: {
    paused?: boolean;
    reason?: string | null;
    mouse_idle?: boolean;
    keyboard_idle?: boolean;
    task_id?: string | null;
  };
  queue?: { pending: number };
}

export interface StepLog {
  stepNumber: number;
  total: number;
  description: string;
  timestamp: number;
  success?: boolean;
}

export interface ThinkingSummary {
  intent?: string;
  reasoningDepth?: string;
  confidenceScore?: number;
  uncertaintyScore?: number;
  riskScore?: number;
  complexityScore?: number;
  ambiguityScore?: number;
  planningDepth?: number;
  checklistScore?: number;
  checklist?: Array<{ id: string; label: string; status: string }>;
  tokenUsage?: number;
  tokenBalance?: number;
  objectives?: string[];
  recoveryStrategies?: string[];
  toolReflectionCount?: number;
  suggestedTools?: string[];
  showChecklist?: boolean;
  showThinkingPanel?: boolean;
}

export interface WorkflowItem {
  id: string;
  name: string;
  run_count: number;
}

export interface ChatAttachment {
  id: string;
  kind: 'image';
  name: string;
  mime: string;
  size: number;
  dataUrl?: string;
  width?: number;
  height?: number;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  status?: 'sending' | 'streaming' | 'done' | 'error' | 'cancelled';
  error?: string;
  source?: 'rust-lm-proxy' | 'python-backend' | 'local';
  attachments?: ChatAttachment[];
  taskId?: string;
  taskStatus?: 'queued' | 'executing' | 'completed' | 'failed';
  taskSteps?: StepLog[];
  thinkingStartedAt?: number;
  thinkingElapsedMs?: number;
  thinkingSummary?: ThinkingSummary;
}
