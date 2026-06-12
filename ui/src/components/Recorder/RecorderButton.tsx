/**
 * RecorderButton — one-click "DNA Recorder".
 *
 * Press once to start recording your actions.
 * Press again to stop and synthesize a reusable agent workflow.
 * The synthesized plan is saved to disk and appears in the Workflows tab.
 */
import { useState } from 'react';
import { useAgentStore } from '../../store/agentStore';
import { apiFetch } from '../../lib/apiClient';
import './RecorderButton.css';

export function RecorderButton() {
  const { isRecording, setRecording } = useAgentStore();
  const [phase, setPhase] = useState<'idle' | 'recording' | 'synthesizing'>('idle');
  const [taskName, setTaskName] = useState('');
  const [showNameInput, setShowNameInput] = useState(false);
  const [lastResult, setLastResult] = useState<{ steps: number; file?: string } | null>(null);

  const handleClick = async () => {
    if (phase === 'idle') {
      setShowNameInput(true);
      return;
    }
    if (phase === 'recording') {
      await stopAndSynthesize();
    }
  };

  const startRecording = async () => {
    setShowNameInput(false);
    const name = taskName.trim() || 'Recorded Task';
    setTaskName(name);
    try {
      const response = await apiFetch('/api/recorder/start', {
        method: 'POST',
        body: JSON.stringify({ task_name: name }),
      });
      if (!response.ok) throw new Error(`Recorder start failed: HTTP ${response.status}`);
      setPhase('recording');
      setRecording(true);
      setLastResult(null);
    } catch (e) {
      console.error('recorder start error:', e);
    }
  };

  const stopAndSynthesize = async () => {
    setPhase('synthesizing');
    try {
      const stopResponse = await apiFetch('/api/recorder/stop', { method: 'POST' });
      if (!stopResponse.ok) throw new Error(`Recorder stop failed: HTTP ${stopResponse.status}`);
      const res = await apiFetch('/api/recorder/synthesize', {
        method: 'POST',
        body: JSON.stringify({ task_name: taskName || 'Recorded Task' }),
      });
      if (!res.ok) throw new Error(`Recorder synthesis failed: HTTP ${res.status}`);
      const data = await res.json();
      if (data.ok) {
        setLastResult({ steps: data.steps });
      }
    } catch (e) {
      console.error('recorder synthesize error:', e);
    } finally {
      setPhase('idle');
      setRecording(false);
      setTaskName('');
    }
  };

  return (
    <div className="recorder-wrapper">
      {showNameInput && (
        <div className="recorder-name-popup">
          <input
            className="recorder-name-input"
            placeholder="Task name (e.g. 'Export CRM report')"
            value={taskName}
            onChange={(e) => setTaskName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && startRecording()}
            autoFocus
          />
          <button className="recorder-name-confirm" onClick={startRecording}>Start</button>
          <button className="recorder-name-cancel" onClick={() => setShowNameInput(false)}>✕</button>
        </div>
      )}

      <button
        className={`recorder-btn ${phase}`}
        onClick={handleClick}
        title={
          phase === 'idle' ? 'Record a task (DNA Recorder)' :
          phase === 'recording' ? 'Stop recording & synthesize workflow' :
          'Synthesizing…'
        }
        disabled={phase === 'synthesizing'}
      >
        {phase === 'idle' && <span className="recorder-icon">⏺</span>}
        {phase === 'recording' && <span className="recorder-icon recording-pulse">⏹</span>}
        {phase === 'synthesizing' && <span className="recorder-icon spin">⟳</span>}
        <span className="recorder-label">
          {phase === 'idle' ? 'Record' :
           phase === 'recording' ? 'Stop & Save' :
           'Synthesizing…'}
        </span>
      </button>

      {lastResult && (
        <div className="recorder-result">
          Workflow saved — {lastResult.steps} steps
        </div>
      )}
    </div>
  );
}
