import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowRight, FolderCode, ShieldCheck, UserRound, X } from 'lucide-react';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import RealismButton from '@/components/ui/shiny-borders-button';

export interface DeveloperProfile {
  displayName: string;
  workspace: string;
}

interface CanvasRevealEffectProps {
  animationSpeed?: number;
  opacities?: number[];
  colors?: number[][];
  containerClassName?: string;
  dotSize?: number;
  showGradient?: boolean;
  reverse?: boolean;
}

function seededNoise(x: number, y: number) {
  const value = Math.sin(x * 12.9898 + y * 78.233) * 43758.5453;
  return value - Math.floor(value);
}

export function CanvasRevealEffect({
  animationSpeed = 3,
  opacities = [0.08, 0.12, 0.18, 0.24, 0.34, 0.42],
  colors = [[249, 115, 22], [251, 146, 60]],
  containerClassName,
  dotSize = 2.4,
  showGradient = true,
  reverse = false,
}: CanvasRevealEffectProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const paletteKey = useMemo(() => colors.flat().join(','), [colors]);
  const opacityKey = useMemo(() => opacities.join(','), [opacities]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const parent = canvas?.parentElement;
    if (!canvas || !parent) return undefined;

    const context = canvas.getContext('2d');
    if (!context) return undefined;

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let width = 0;
    let height = 0;
    let frame = 0;
    let startedAt = performance.now();
    let lastDrawAt = 0;

    const resize = () => {
      const rect = parent.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = Math.max(1, rect.width);
      height = Math.max(1, rect.height);
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      startedAt = performance.now();
    };

    const draw = (now: number) => {
      if (!reducedMotion && now - lastDrawAt < 1000 / 24) {
        frame = requestAnimationFrame(draw);
        return;
      }
      lastDrawAt = now;
      const elapsed = (now - startedAt) / 1000;
      const grid = 18;
      const centerX = width * 0.5;
      const centerY = height * 0.46;
      const maxDistance = Math.hypot(centerX, centerY);
      const reveal = Math.min(1, elapsed * animationSpeed * 0.22);

      context.clearRect(0, 0, width, height);

      for (let y = grid / 2; y < height; y += grid) {
        for (let x = grid / 2; x < width; x += grid) {
          const distance = Math.hypot(x - centerX, y - centerY) / maxDistance;
          const random = seededNoise(x / grid, y / grid);
          const threshold = reverse ? 1 - distance : distance;
          const revealed = reducedMotion || reveal >= threshold * 0.78 + random * 0.18;
          if (!revealed) continue;

          const pulse = reducedMotion ? 0.7 : 0.55 + Math.sin(elapsed * animationSpeed + random * 8) * 0.25;
          const opacity = opacities[Math.floor(random * opacities.length)] ?? 0.18;
          const color = colors[Math.floor(random * colors.length)] ?? colors[0] ?? [249, 115, 22];
          context.fillStyle = `rgba(${color[0]}, ${color[1]}, ${color[2]}, ${Math.max(0, opacity * pulse)})`;
          context.beginPath();
          context.arc(x, y, dotSize, 0, Math.PI * 2);
          context.fill();
        }
      }

      if (!reducedMotion) frame = requestAnimationFrame(draw);
    };

    const observer = new ResizeObserver(resize);
    observer.observe(parent);
    resize();
    draw(performance.now());

    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [animationSpeed, dotSize, opacityKey, paletteKey, reverse]);

  return (
    <div className={cn('pointer-events-none absolute inset-0 overflow-hidden', containerClassName)}>
      <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" aria-hidden="true" />
      {showGradient ? (
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,transparent_0%,rgba(5,5,5,0.2)_42%,rgba(5,5,5,0.94)_100%)]" />
      ) : null}
    </div>
  );
}

interface SignInPageProps {
  className?: string;
  onComplete: (profile: DeveloperProfile) => void;
  onDismiss?: () => void;
}

export function SignInPage({ className, onComplete, onDismiss }: SignInPageProps) {
  const [displayName, setDisplayName] = useState('');
  const [workspace, setWorkspace] = useState('Mi espacio local');
  const [ready, setReady] = useState(false);

  const profile = useMemo(
    () => ({
      displayName: displayName.trim() || 'Developer',
      workspace: workspace.trim() || 'Mi espacio local',
    }),
    [displayName, workspace],
  );

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    setReady(true);
  };

  return (
    <motion.section
      className={cn(
        'relative z-10 w-full max-w-[520px] overflow-hidden rounded-3xl border border-orange-400/20 bg-[#0d0d0e]/95 shadow-[0_28px_100px_rgba(0,0,0,0.62)]',
        className,
      )}
      initial={{ opacity: 0, scale: 0.96, y: 18 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      transition={{ type: 'spring', damping: 24, stiffness: 280 }}
    >
      <CanvasRevealEffect
        animationSpeed={3.2}
        colors={[[249, 115, 22], [251, 146, 60], [212, 212, 216]]}
        dotSize={2}
      />

      {onDismiss ? (
        <button
          type="button"
          onClick={onDismiss}
          className="absolute right-4 top-4 z-20 grid h-9 w-9 place-items-center rounded-xl border border-white/10 bg-black/35 text-zinc-500 transition hover:bg-white/[0.06] hover:text-zinc-100"
          aria-label="Cerrar bienvenida"
        >
          <X size={16} />
        </button>
      ) : null}

      <div className="relative z-10 p-7 sm:p-9">
        {!ready ? (
          <motion.form
            onSubmit={handleSubmit}
            initial={{ opacity: 0, x: -24 }}
            animate={{ opacity: 1, x: 0 }}
            className="space-y-6"
          >
            <div className="space-y-2 pr-10">
              <span className="inline-flex items-center gap-2 rounded-full border border-orange-400/20 bg-orange-400/10 px-3 py-1 text-[10px] font-bold uppercase tracking-[0.14em] text-orange-300">
                <ShieldCheck size={13} />
                Perfil local
              </span>
              <h1 className="text-3xl font-bold tracking-[-0.035em] text-white">Bienvenido a AgentMax</h1>
              <p className="max-w-md text-sm leading-6 text-zinc-400">
                Personaliza tu espacio de desarrollo. Estos datos quedan solo en este dispositivo y no crean una cuenta.
              </p>
            </div>

            <div className="space-y-3">
              <label className="block space-y-1.5">
                <span className="flex items-center gap-2 text-xs font-semibold text-zinc-300">
                  <UserRound size={14} className="text-orange-400" />
                  Cómo debería llamarte AgentMax
                </span>
                <input
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                  placeholder="Developer"
                  autoFocus
                  className="h-12 w-full rounded-xl border border-white/10 bg-black/45 px-4 text-sm text-white outline-none transition placeholder:text-zinc-700 focus:border-orange-400/55 focus:ring-4 focus:ring-orange-400/10"
                />
              </label>

              <label className="block space-y-1.5">
                <span className="flex items-center gap-2 text-xs font-semibold text-zinc-300">
                  <FolderCode size={14} className="text-orange-400" />
                  Espacio de trabajo
                </span>
                <input
                  value={workspace}
                  onChange={(event) => setWorkspace(event.target.value)}
                  placeholder="Mi espacio local"
                  className="h-12 w-full rounded-xl border border-white/10 bg-black/45 px-4 text-sm text-white outline-none transition placeholder:text-zinc-700 focus:border-orange-400/55 focus:ring-4 focus:ring-orange-400/10"
                />
              </label>
            </div>

            <RealismButton type="submit" className="w-full">
              Preparar AgentMax
              <ArrowRight size={16} />
            </RealismButton>
          </motion.form>
        ) : (
          <motion.div
            initial={{ opacity: 0, x: 24 }}
            animate={{ opacity: 1, x: 0 }}
            className="space-y-7 py-3 text-center"
          >
            <div className="mx-auto grid h-16 w-16 place-items-center rounded-2xl border border-orange-400/25 bg-orange-400/10 text-orange-300 shadow-agentmax-glow">
              <ShieldCheck size={30} />
            </div>
            <div className="space-y-2">
              <h2 className="text-2xl font-bold text-white">Espacio listo, {profile.displayName}</h2>
              <p className="text-sm leading-6 text-zinc-400">
                Configuraremos modelos, permisos y ComputerMax para <strong className="text-zinc-200">{profile.workspace}</strong>.
              </p>
            </div>
            <RealismButton className="w-full" onClick={() => onComplete(profile)}>
              Continuar
              <ArrowRight size={16} />
            </RealismButton>
          </motion.div>
        )}
      </div>
    </motion.section>
  );
}
