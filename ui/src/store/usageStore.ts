import { create } from 'zustand';
import type { BillingPlan } from '../config/billingLimits';
import {
  canSpend,
  consumeTokens,
  estimateTokens,
  loadUsageSnapshot,
  remainingDaily,
  resetLocalUsage,
  setPlan,
  type SpendDecision,
  type UsageSnapshot,
} from '../lib/tokenService';

interface UsageStore {
  snapshot: UsageSnapshot;
  hydrate: () => void;
  setPlan: (plan: BillingPlan) => void;
  estimate: (text: string, attachments?: number, tools?: number) => number;
  canSpend: (tokens: number) => SpendDecision;
  consume: (tokens: number) => void;
  reset: () => void;
  remainingDaily: () => number | null;
}

export const useUsageStore = create<UsageStore>((set, get) => ({
  snapshot: loadUsageSnapshot(),
  hydrate: () => set({ snapshot: loadUsageSnapshot() }),
  setPlan: (plan) => set((state) => ({ snapshot: setPlan(state.snapshot, plan) })),
  estimate: estimateTokens,
  canSpend: (tokens) => canSpend(get().snapshot, tokens),
  consume: (tokens) => set((state) => ({ snapshot: consumeTokens(state.snapshot, tokens) })),
  reset: () => set((state) => ({ snapshot: resetLocalUsage(state.snapshot.plan) })),
  remainingDaily: () => remainingDaily(get().snapshot),
}));
