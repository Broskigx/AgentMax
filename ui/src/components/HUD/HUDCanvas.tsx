import { useEffect, useRef, useState } from 'react';
import { useAgentStore } from '../../store/agentStore';

// ── Simple 3D-like HUD using CSS transforms (no Three.js dependency) ─────────
// We keep @react-three/fiber/drei in package.json as optional — if the user has
// a GPU that supports WebGL, the full 3D HUD can be enabled. For now we render
// a performant CSS-based particle field + radar that works on all machines.

const styles = {
  canvas: {
    position: 'fixed' as const, inset: 0, zIndex: 9998,
    pointerEvents: 'none' as const,
    background: 'transparent',
  },
  radarRing: {
    position: 'absolute' as const, borderRadius: '50%',
    border: '1px solid rgba(91,140,255,0.08)',
  },
  scanLine: {
    position: 'absolute' as const, height: 1,
    background: 'linear-gradient(90deg, transparent, rgba(91,140,255,0.2), transparent)',
    opacity: 0.3,
  },
  statusDot: (color: string, delay: number) => ({
    position: 'absolute' as const, width: 4, height: 4,
    borderRadius: '50%', background: color,
    boxShadow: `0 0 8px ${color}44`,
    animation: `hud-pulse 2s ease-in-out ${delay}s infinite`,
  }),
  corner: {
    position: 'absolute' as const, width: 20, height: 20,
    borderColor: 'rgba(91,140,255,0.12)',
    borderStyle: 'solid' as const,
  },
};

function ParticleField() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const particles = useRef<Array<{ x: number; y: number; vx: number; vy: number; size: number; alpha: number }>>([]);
  const mouseRef = useRef({ x: 0, y: 0 });

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const resize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
    };
    resize();
    window.addEventListener('resize', resize);

    // Init particles
    const count = Math.min(40, Math.floor((window.innerWidth * window.innerHeight) / 40000));
    particles.current = Array.from({ length: count }, () => ({
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      vx: (Math.random() - 0.5) * 0.3,
      vy: (Math.random() - 0.5) * 0.3,
      size: Math.random() * 1.5 + 0.5,
      alpha: Math.random() * 0.3 + 0.1,
    }));

    let running = true;
    let animId = 0;

    const animate = () => {
      if (!running) return;
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      const connectedLines: Array<{ x1: number; y1: number; x2: number; y2: number; alpha: number }> = [];

      for (const p of particles.current) {
        p.x += p.vx;
        p.y += p.vy;
        if (p.x < 0) p.x = canvas.width;
        if (p.x > canvas.width) p.x = 0;
        if (p.y < 0) p.y = canvas.height;
        if (p.y > canvas.height) p.y = 0;

        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(91, 140, 255, ${p.alpha})`;
        ctx.fill();

        // Mouse proximity connection
        const dx = p.x - mouseRef.current.x;
        const dy = p.y - mouseRef.current.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < 150) {
          connectedLines.push({
            x1: p.x, y1: p.y,
            x2: mouseRef.current.x, y2: mouseRef.current.y,
            alpha: (1 - dist / 150) * 0.3,
          });
        }
      }

      // Draw connections
      for (const line of connectedLines) {
        ctx.beginPath();
        ctx.moveTo(line.x1, line.y1);
        ctx.lineTo(line.x2, line.y2);
        ctx.strokeStyle = `rgba(91, 140, 255, ${line.alpha})`;
        ctx.lineWidth = 0.5;
        ctx.stroke();
      }

      animId = requestAnimationFrame(animate);
    };

    const handleMouse = (e: MouseEvent) => {
      mouseRef.current = { x: e.clientX, y: e.clientY };
    };
    window.addEventListener('mousemove', handleMouse);

    animate();

    return () => {
      running = false;
      cancelAnimationFrame(animId);
      window.removeEventListener('resize', resize);
      window.removeEventListener('mousemove', handleMouse);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      style={{
        position: 'fixed' as const, inset: 0,
        pointerEvents: 'none' as const, opacity: 0.6,
      }}
    />
  );
}

// ── HUDCanvas ────────────────────────────────────────────────────────────────

export function HUDCanvas() {
  const { agentState, isTyping } = useAgentStore();
  const [visible, setVisible] = useState(true);

  // Hide HUD after task completes
  useEffect(() => {
    if (agentState === 'completed' || agentState === 'idle') {
      const timer = setTimeout(() => setVisible(false), 3000);
      return () => clearTimeout(timer);
    }
    setVisible(true);
  }, [agentState]);

  if (!visible) return null;

  const stateColor = agentState === 'working' ? '#5b8cff' :
                     agentState === 'validating' ? '#fbbf24' :
                     agentState === 'completed' ? '#4ade80' :
                     agentState === 'error' ? '#f87171' : '#4ade80';

  const size = 200;

  return (
    <div style={styles.canvas}>
      <ParticleField />

      {/* Scanning lines */}
      <div style={{
        ...styles.scanLine, top: '20%', left: '5%', width: '90%',
        animation: 'hud-scan 4s ease-in-out infinite',
      }} />
      <div style={{
        ...styles.scanLine, top: '50%', left: '5%', width: '90%',
        animation: 'hud-scan 4s ease-in-out 1.3s infinite',
      }} />
      <div style={{
        ...styles.scanLine, top: '80%', left: '5%', width: '90%',
        animation: 'hud-scan 4s ease-in-out 2.6s infinite',
      }} />

      {/* Radar rings - bottom right */}
      <div style={{ position: 'absolute', bottom: 40, right: 40 }}>
        <div style={{ ...styles.radarRing, width: size, height: size, opacity: 0.15 }} />
        <div style={{ ...styles.radarRing, width: size * 0.7, height: size * 0.7, top: size * 0.15, left: size * 0.15, opacity: 0.1 }} />
        <div style={{ ...styles.radarRing, width: size * 0.4, height: size * 0.4, top: size * 0.3, left: size * 0.3, opacity: 0.08 }} />

        {/* Status dot */}
        <div style={{
          ...styles.statusDot(stateColor, 0),
          top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
          width: 6, height: 6,
        }} />
      </div>

      {/* Corner brackets */}
      <div style={{ ...styles.corner, top: 16, left: 16, borderWidth: '1px 0 0 1px' }} />
      <div style={{ ...styles.corner, top: 16, right: 16, borderWidth: '1px 1px 0 0' }} />
      <div style={{ ...styles.corner, bottom: 16, left: 16, borderWidth: '0 0 1px 1px' }} />
      <div style={{ ...styles.corner, bottom: 16, right: 16, borderWidth: '0 1px 1px 0' }} />

      {/* Status text - bottom left */}
      <div style={{
        position: 'absolute', bottom: 40, left: 40,
        fontFamily: 'JetBrains Mono, monospace', fontSize: 10,
        color: stateColor, opacity: 0.5,
        display: 'flex', flexDirection: 'column', gap: 4,
      }}>
        <span>AgentMax // HUD</span>
        <span style={{ color: '#6b7a8d' }}>
          STATE: {agentState.toUpperCase()}
        </span>
        {isTyping && (
          <span style={{ color: '#5b8cff' }}>STREAMING...</span>
        )}
      </div>


    </div>
  );
}
