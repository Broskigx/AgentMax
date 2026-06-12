import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

// Only load the obfuscator in production builds to keep dev iteration fast.
function obfuscatorPlugin(): Plugin {
  const isProduction = process.env.NODE_ENV === 'production' && !process.env.TAURI_DEBUG;

  return {
    name: 'AgentMax-obfuscator',
    // Runs after the bundle is assembled (Rollup renderChunk phase).
    async renderChunk(code, chunk) {
      if (!isProduction) return null;
      // Skip vendor chunks — obfuscating third-party code causes false-positive
      // AV hits and can break framework internals (React reconciler relies on
      // function.name and specific prototype shapes).
      const vendorChunks = ['react-vendor', 'framer', 'three', 'lucide', 'msgpack', 'xterm', 'markdown', 'highlight'];
      if (vendorChunks.some(v => chunk.fileName.includes(v))) return null;

      let JavaScriptObfuscator: any = null;
      try {
        JavaScriptObfuscator = (await import('javascript-obfuscator')).default;
      } catch {
        console.warn('[AgentMax-obfuscator] javascript-obfuscator not installed, skipping obfuscation');
        return null;
      }
      const result = JavaScriptObfuscator.obfuscate(code, {
        // ── String protection (maximum) ────────────────────────────────────
        stringArray: true,
        stringArrayEncoding: ['rc4', 'base64'],
        stringArrayThreshold: 1.0,
        rotateStringArray: true,
        shuffleStringArray: true,
        splitStrings: true,
        splitStringsChunkLength: 3,
        stringArrayIndexShift: true,
        stringArrayIndexesType: ['hexadecimal-number'],
        stringArrayWrappersCount: 3,
        stringArrayWrappersChainedCalls: true,
        stringArrayWrappersParametersMaxCount: 4,
        stringArrayWrappersType: 'variable',
        // ── Identifier renaming (aggressive) ──────────────────────────────
        identifierNamesGenerator: 'mangled-shuffled',
        renameGlobals: false,
        // Do not rename properties across app/vendor boundaries. The vendor
        // chunks are intentionally not obfuscated, so property mangling here can
        // turn React.Component into a non-existent vendor property at runtime.
        renameProperties: false,
        // ── Code transformations (full) ───────────────────────────────────
        controlFlowFlattening: true,
        controlFlowFlatteningThreshold: 0.85,
        deadCodeInjection: true,
        deadCodeInjectionThreshold: 0.6,
        selfDefending: true,
        // ── Debug/console hardening ────────────────────────────────────────
        disableConsoleOutput: true,
        debugProtection: true,
        debugProtectionInterval: 4000,
        // ── Obscuring ─────────────────────────────────────────────────────
        compact: true,
        simplify: true,
        variablesPrefix: '\u200b',
        transformObjectKeys: true,
        unicodeEscapeSequence: true,
        identifierNamesCache: {},
        seed: 0,
        // ── Misc ──────────────────────────────────────────────────────────
        target: 'browser',
        sourceMap: false,
        log: false,
        inputFileName: chunk.fileName,
      });
      return { code: result.getObfuscatedCode(), map: null };
    },
  };
}

export default defineConfig({
  plugins: [react(), obfuscatorPlugin()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: { ignored: ['**/src-tauri/**'] },
  },
  envPrefix: ['VITE_', 'TAURI_'],
  build: {
    target: ['es2021', 'chrome100', 'safari13'],
    minify: !process.env.TAURI_DEBUG ? 'esbuild' : false,
    sourcemap: !!process.env.TAURI_DEBUG,
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules')) {
            if (id.includes('react') || id.includes('scheduler')) return 'react-vendor';
            if (id.includes('framer-motion')) return 'framer';
            if (id.includes('three') || id.includes('@react-three')) return 'three';
            if (id.includes('lucide')) return 'lucide';
            if (id.includes('msgpack')) return 'msgpack';
            if (id.includes('xterm')) return 'xterm';
            if (id.includes('markdown') || id.includes('remark') || id.includes('rehype')) return 'markdown';
            if (id.includes('highlight.js')) return 'highlight';
          }
        },
      },
    },
  },
  optimizeDeps: {
    include: ['react', 'react-dom', 'framer-motion', 'zustand'],
  },
});
