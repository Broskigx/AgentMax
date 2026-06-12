import type { ExperienceMode } from '../types';

export interface AdvancedAISettings {
  temperature: number;
  maxTokens: number;
  contextTurns: number;
  autoTools: boolean;
  verboseReasoning: boolean;
}

export interface UserPreferences {
  experienceMode: ExperienceMode;
  advancedAISettings: AdvancedAISettings;
  selectedModel: string;
  activeBackend: string;
}

const STORAGE_KEY = 'AgentMax.prefs.v1';

const DEFAULTS: UserPreferences = {
  experienceMode: 'normal',
  advancedAISettings: {
    temperature: 0.4,
    maxTokens: 2048,
    contextTurns: 12,
    autoTools: true,
    verboseReasoning: false,
  },
  selectedModel: '',
  activeBackend: 'AgentMax',
};

export function loadUserPreferences(): UserPreferences {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULTS };
    const parsed = JSON.parse(raw) as Partial<UserPreferences>;
    return {
      experienceMode: parsed.experienceMode ?? DEFAULTS.experienceMode,
      advancedAISettings: {
        ...DEFAULTS.advancedAISettings,
        ...(parsed.advancedAISettings ?? {}),
      },
      selectedModel: parsed.selectedModel ?? DEFAULTS.selectedModel,
      activeBackend: parsed.activeBackend ?? DEFAULTS.activeBackend,
    };
  } catch {
    return { ...DEFAULTS };
  }
}

export function saveUserPreferences(prefs: Partial<UserPreferences>): void {
  try {
    const current = loadUserPreferences();
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ ...current, ...prefs }),
    );
  } catch {
    // Restricted webview or private mode
  }
}