export type BillingPlan = 'Free' | 'Starter' | 'Pro' | 'Local';

export interface BillingPlanConfig {
  name: BillingPlan;
  dailyTokens: number | null;
  monthlyTokens: number | null;
  maxTaskTokens: number;
  localBypass: boolean;
  description: string;
}

export const BILLING_LIMITS: Record<BillingPlan, BillingPlanConfig> = {
  Free: {
    name: 'Free',
    dailyTokens: 250,
    monthlyTokens: 3_000,
    maxTaskTokens: 120,
    localBypass: false,
    description: 'Pruebas pequenas con herramientas limitadas.',
  },
  Starter: {
    name: 'Starter',
    dailyTokens: 2_000,
    monthlyTokens: 45_000,
    maxTaskTokens: 600,
    localBypass: false,
    description: 'Beta tecnica con uso diario moderado.',
  },
  Pro: {
    name: 'Pro',
    dailyTokens: 10_000,
    monthlyTokens: 250_000,
    maxTaskTokens: 2_000,
    localBypass: false,
    description: 'Trabajo continuo con tareas mas largas.',
  },
  Local: {
    name: 'Local',
    dailyTokens: null,
    monthlyTokens: null,
    maxTaskTokens: 8_000,
    localBypass: true,
    description: 'Modelo local propio; registra uso pero no bloquea por creditos.',
  },
};

export const DEFAULT_BILLING_PLAN: BillingPlan = 'Free';
