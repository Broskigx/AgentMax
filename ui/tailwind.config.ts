import type { Config } from 'tailwindcss';

export default {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  corePlugins: {
    // AgentMax already owns its global reset and desktop-window defaults.
    preflight: false,
  },
  theme: {
    extend: {
      colors: {
        background: 'var(--am-bg)',
        foreground: 'var(--am-text)',
        border: 'var(--am-border)',
        muted: 'var(--am-surface-hover)',
        'muted-foreground': 'var(--am-text-muted)',
        primary: 'var(--am-orange-400)',
      },
      fontFamily: {
        sans: ['var(--am-font-sans)'],
        mono: ['var(--am-font-mono)'],
      },
      boxShadow: {
        'agentmax-glow': '0 0 30px rgba(249, 115, 22, 0.18)',
      },
    },
  },
  plugins: [],
} satisfies Config;
