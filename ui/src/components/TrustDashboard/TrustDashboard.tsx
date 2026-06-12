/**
 * TrustDashboard — Visual Auditor sidebar.
 *
 * Shows a real-time "attention heatmap" of what the agent is seeing and
 * what it's about to do, rendered as colored rectangles over a thumbnail
 * of the live screen.
 *
 * Color key (matches trust_heatmap.py):
 *   Deep green  (#22c55e) — accessibility API element (OS-verified)
 *   Teal        (#06b6d4) — OCR-read text
 *   Indigo      (#6366f1) — visual memory template match
 *   Red         (#ef4444) — next action target (about to click/type)
 *   Amber       (#f59e0b) — low-confidence element (< 60%)
 */
import { useEffect, useRef, useState } from 'react';
import { decodeIpcEvents } from '../../lib/ipcMessages';
import { useAgentStore } from '../../store/agentStore';
import './TrustDashboard.css';

interface Region {
  x: number; y: number; w: number; h: number;
  color: string; label: string; confidence: number; source: string;
}

interface TrustSnapshot {
  screenshot_b64: string;
  regions: Region[];
  active_window: string;
}

const SOURCE_LABELS: Record<string, string> = {
  accessibility: 'Accessibility',
  ocr:           'OCR text',
  template:      'Visual memory',
  action_target: 'Next action',
  uncertain:     'Uncertain',
};

export function TrustDashboard() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [snapshot, setSnapshot] = useState<TrustSnapshot | null>(null);
  const [actionLabel, setActionLabel] = useState('');

  // Subscribe to WS events from the store
  const { ws } = useAgentStore();

  useEffect(() => {
    if (!ws) return;

    const handler = async (e: MessageEvent) => {
      try {
        const events = await decodeIpcEvents(e.data);

        events.forEach(({ topic, payload }) => {
          if (topic === 'vision.attention' && payload) {
            setSnapshot({
              screenshot_b64: payload.screenshot_b64 as string,
              regions: payload.regions as Region[],
              active_window: '',
            });
          }

          if (topic === 'task.action_preview' && payload) {
            setActionLabel((payload.label as string) || '');
            // Merge action_target region into existing snapshot
            if (payload.bounds) {
              const [x, y, w, h] = payload.bounds as number[];
              setSnapshot((prev) => {
                if (!prev) return prev;
                const sw = 1920; const sh = 1080; // approximation, normalized anyway
                return {
                  ...prev,
                  regions: [
                    ...prev.regions.filter((r) => r.source !== 'action_target'),
                    { x: x/sw, y: y/sh, w: w/sw, h: h/sh,
                      color: '#ef4444', label: payload.label as string,
                      confidence: 1, source: 'action_target' },
                  ],
                };
              });
            }
          }
        });
      } catch { /* ignore */ }
    };

    ws.addEventListener('message', handler);
    return () => ws.removeEventListener('message', handler);
  }, [ws]);

  // Render screenshot + regions onto canvas
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !snapshot) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const cw = canvas.width;
    const ch = canvas.height;

    const draw = () => {
      ctx.clearRect(0, 0, cw, ch);

      // Background screenshot
      if (imgRef.current && imgRef.current.complete) {
        ctx.drawImage(imgRef.current, 0, 0, cw, ch);
      } else {
        ctx.fillStyle = '#0a0a10';
        ctx.fillRect(0, 0, cw, ch);
      }

      // Regions
      for (const r of snapshot.regions) {
        const rx = r.x * cw, ry = r.y * ch;
        const rw = r.w * cw, rh = r.h * ch;

        ctx.strokeStyle = r.color;
        ctx.lineWidth = r.source === 'action_target' ? 2.5 : 1.5;
        ctx.shadowColor = r.color;
        ctx.shadowBlur = r.source === 'action_target' ? 8 : 4;

        // Fill with transparent color
        ctx.fillStyle = r.color + (r.source === 'action_target' ? '33' : '18');
        ctx.fillRect(rx, ry, rw, rh);
        ctx.strokeRect(rx, ry, rw, rh);

        // Label for large enough regions
        if (rw > 40 && rh > 14 && r.label) {
          ctx.shadowBlur = 0;
          ctx.fillStyle = '#000000aa';
          ctx.fillRect(rx + 1, ry + 1, Math.min(rw - 2, r.label.length * 6 + 4), 13);
          ctx.fillStyle = r.color;
          ctx.font = '10px monospace';
          ctx.fillText(r.label.slice(0, 24), rx + 3, ry + 11);
        }
      }
    };

    // Load screenshot image
    if (snapshot.screenshot_b64) {
      const img = new Image();
      img.onload = () => {
        imgRef.current = img;
        draw();
      };
      img.src = `data:image/png;base64,${snapshot.screenshot_b64}`;
    } else {
      draw();
    }
  }, [snapshot]);

  const regionCounts = snapshot
    ? Object.entries(
        snapshot.regions.reduce<Record<string, number>>((acc, r) => {
          acc[r.source] = (acc[r.source] || 0) + 1;
          return acc;
        }, {})
      )
    : [];

  return (
    <div className="trust-dashboard">
      <div className="trust-header">
        <span className="trust-title">Visual Auditor</span>
        {actionLabel && (
          <span className="trust-action-badge">→ {actionLabel}</span>
        )}
      </div>

      <canvas
        ref={canvasRef}
        className="trust-canvas"
        width={320}
        height={180}
      />

      {snapshot && (
        <div className="trust-legend">
          {regionCounts.map(([source, count]) => (
            <div key={source} className="trust-legend-item">
              <span
                className="trust-legend-dot"
                style={{ background: getSourceColor(source) }}
              />
              <span className="trust-legend-label">
                {SOURCE_LABELS[source] ?? source}
              </span>
              <span className="trust-legend-count">{count}</span>
            </div>
          ))}
        </div>
      )}

      {!snapshot && (
        <div className="trust-empty">
          Agent idle — no screen analysis yet
        </div>
      )}
    </div>
  );
}

function getSourceColor(source: string): string {
  const map: Record<string, string> = {
    accessibility: '#22c55e',
    ocr:           '#06b6d4',
    template:      '#6366f1',
    action_target: '#ef4444',
    uncertain:     '#f59e0b',
  };
  return map[source] ?? '#888';
}
