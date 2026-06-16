import {
  BILLING_LIMITS,
  DEFAULT_BILLING_PLAN,
  type BillingPlan,
} from '../config/billingLimits';

/**
 * Client-side token budget tracking.
 *
 * Mirrors the server-side budget enforced by core/security/token_manager.py,
 * but runs in the renderer so the UI can show remaining credits and gate
 * actions before they reach the backend. Persisted to localStorage and rolled
 * over automatically at day / month boundaries.
 */

const STORAGE_KEY = 'agentmax.usage.v1';

/** Rough chars-per-token ratio for English/Spanish prose. */
const CHARS_PER_TOKEN = 4;
/** Approximate prompt overhead added per attached image. */
const TOKENS_PER_ATTACHMENT = 85;
/** Approximate prompt overhead added per exposed tool definition. */
const TOKENS_PER_TOOL = 60;

export interface UsageSnapshot {
  plan: BillingPlan;
  /** Tokens consumed during the current day window. */
  dailyUsed: number;
  /** Tokens consumed during the current month window. */
  monthlyUsed: number;
  /** Day bucket key (YYYY-MM-DD) used to detect daily rollover. */
  dayKey: string;
  /** Month bucket key (YYYY-MM) used to detect monthly rollover. */
  monthKey: string;
  /** Epoch milliseconds of the last update. */
  updatedAt: number;
}

export interface SpendDecision {
  allowed: boolean;
  reason?: string;
  /** Remaining daily tokens, or null when the plan is unlimited. */
  remainingDaily: number | null;
  /** Remaining monthly tokens, or null when the plan is unlimited. */
  remainingMonthly: number | null;
  /** Per-task hard cap for the active plan. */
  maxTaskTokens: number;
}

function dayKey(d: Date = new Date()): string {
  return d.toISOString().slice(0, 10); // YYYY-MM-DD
}

function monthKey(d: Date = new Date()): string {
  return d.toISOString().slice(0, 7); // YYYY-MM
}

function freshSnapshot(plan: BillingPlan): UsageSnapshot {
  return {
    plan,
    dailyUsed: 0,
    monthlyUsed: 0,
    dayKey: dayKey(),
    monthKey: monthKey(),
    updatedAt: Date.now(),
  };
}

/** Reset the daily and/or monthly counters when their window has elapsed. */
function applyRollover(snapshot: UsageSnapshot): UsageSnapshot {
  const today = dayKey();
  const thisMonth = monthKey();
  if (snapshot.dayKey === today && snapshot.monthKey === thisMonth) {
    return snapshot;
  }
  return {
    ...snapshot,
    dailyUsed: snapshot.dayKey === today ? snapshot.dailyUsed : 0,
    monthlyUsed: snapshot.monthKey === thisMonth ? snapshot.monthlyUsed : 0,
    dayKey: today,
    monthKey: thisMonth,
    updatedAt: Date.now(),
  };
}

function hasStorage(): boolean {
  try {
    return typeof window !== 'undefined' && !!window.localStorage;
  } catch {
    return false;
  }
}

function persist(snapshot: UsageSnapshot): UsageSnapshot {
  if (hasStorage()) {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot));
    } catch {
      /* storage full / unavailable — keep working in-memory */
    }
  }
  return snapshot;
}

export function loadUsageSnapshot(): UsageSnapshot {
  if (hasStorage()) {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as Partial<UsageSnapshot>;
        if (parsed && typeof parsed.plan === 'string' && parsed.plan in BILLING_LIMITS) {
          const merged: UsageSnapshot = {
            ...freshSnapshot(parsed.plan as BillingPlan),
            ...parsed,
            plan: parsed.plan as BillingPlan,
          };
          return persist(applyRollover(merged));
        }
      }
    } catch {
      /* corrupt entry — fall through to a fresh snapshot */
    }
  }
  return persist(freshSnapshot(DEFAULT_BILLING_PLAN));
}

export function setPlan(snapshot: UsageSnapshot, plan: BillingPlan): UsageSnapshot {
  if (!(plan in BILLING_LIMITS)) {
    return snapshot;
  }
  return persist(applyRollover({ ...snapshot, plan, updatedAt: Date.now() }));
}

/** Heuristic token estimate for a prompt plus optional attachments / tools. */
export function estimateTokens(
  text: string,
  attachments = 0,
  tools = 0,
): number {
  const textTokens = Math.ceil((text?.length ?? 0) / CHARS_PER_TOKEN);
  return (
    textTokens +
    Math.max(0, attachments) * TOKENS_PER_ATTACHMENT +
    Math.max(0, tools) * TOKENS_PER_TOOL
  );
}

export function remainingDaily(snapshot: UsageSnapshot): number | null {
  const limits = BILLING_LIMITS[snapshot.plan];
  if (limits.dailyTokens === null) {
    return null;
  }
  const rolled = applyRollover(snapshot);
  return Math.max(0, limits.dailyTokens - rolled.dailyUsed);
}

export function remainingMonthly(snapshot: UsageSnapshot): number | null {
  const limits = BILLING_LIMITS[snapshot.plan];
  if (limits.monthlyTokens === null) {
    return null;
  }
  const rolled = applyRollover(snapshot);
  return Math.max(0, limits.monthlyTokens - rolled.monthlyUsed);
}

export function canSpend(snapshot: UsageSnapshot, tokens: number): SpendDecision {
  const limits = BILLING_LIMITS[snapshot.plan];
  const remDaily = remainingDaily(snapshot);
  const remMonthly = remainingMonthly(snapshot);

  const base: SpendDecision = {
    allowed: true,
    remainingDaily: remDaily,
    remainingMonthly: remMonthly,
    maxTaskTokens: limits.maxTaskTokens,
  };

  if (tokens > limits.maxTaskTokens) {
    return {
      ...base,
      allowed: false,
      reason: `La tarea requiere ${tokens} tokens y supera el maximo por tarea del plan ${snapshot.plan} (${limits.maxTaskTokens}).`,
    };
  }

  // Local / unlimited plans record usage but never block on credits.
  if (limits.localBypass) {
    return base;
  }

  if (remDaily !== null && tokens > remDaily) {
    return {
      ...base,
      allowed: false,
      reason: `Sin creditos diarios suficientes (restan ${remDaily}, se necesitan ${tokens}).`,
    };
  }

  if (remMonthly !== null && tokens > remMonthly) {
    return {
      ...base,
      allowed: false,
      reason: `Sin creditos mensuales suficientes (restan ${remMonthly}, se necesitan ${tokens}).`,
    };
  }

  return base;
}

export function consumeTokens(snapshot: UsageSnapshot, tokens: number): UsageSnapshot {
  const rolled = applyRollover(snapshot);
  const delta = Math.max(0, tokens);
  return persist({
    ...rolled,
    dailyUsed: rolled.dailyUsed + delta,
    monthlyUsed: rolled.monthlyUsed + delta,
    updatedAt: Date.now(),
  });
}

export function resetLocalUsage(plan: BillingPlan): UsageSnapshot {
  return persist(freshSnapshot(plan));
}
