import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { AgentMaxLogo } from '../brand/AgentMaxLogo';
import {
  PromptInputBox,
  type PromptInputBoxHandle,
  type PromptMode,
} from '../ui/ai-prompt-box';
import { useAgentStore } from '../../store/agentStore';
import type { ChatAttachment, ChatMessage, StepLog } from '../../types';
import './ChatThread.css';

function TypingDots() {
  return (
    <div className="ct-typing">
      <div className="ct-typing-avatar" />
      <div className="ct-typing-dots">
        {[0, 1, 2].map((i) => (
          <motion.span
            key={i}
            className="ct-dot"
            animate={{ y: [0, -6, 0], opacity: [0.35, 1, 0.35] }}
            transition={{ duration: 0.9, repeat: Infinity, delay: i * 0.16, ease: 'easeInOut' }}
          />
        ))}
      </div>
    </div>
  );
}

function TaskTimeline({ steps, status }: { steps: StepLog[]; status?: ChatMessage['taskStatus'] }) {
  if (!steps.length && status !== 'queued') return null;

  return (
    <div className="ct-timeline">
      {status === 'queued' && steps.length === 0 ? (
        <div className="ct-timeline-row ct-timeline-row--pending">
          <span className="ct-tl-dot ct-tl-dot--pulse" />
          <span className="ct-tl-desc">Preparando tarea...</span>
        </div>
      ) : null}
      {steps.map((step, index) => {
        const isRunning = index === steps.length - 1 && status === 'executing';
        return (
          <motion.div
            key={`${step.stepNumber}-${step.timestamp}`}
            className="ct-timeline-row"
            initial={{ opacity: 0, x: -8 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ type: 'spring', stiffness: 320, damping: 26, delay: index * 0.05 }}
          >
            <span className={`ct-tl-dot ${isRunning ? 'ct-tl-dot--pulse' : 'ct-tl-dot--done'}`} />
            <span className="ct-tl-num">{step.stepNumber}</span>
            <span className="ct-tl-desc">{step.description}</span>
          </motion.div>
        );
      })}
      {status === 'completed' ? (
        <div className="ct-timeline-row ct-timeline-row--done">
          <span className="ct-tl-check">✓</span>
          <span className="ct-tl-desc ct-tl-desc--done">Tarea completada</span>
        </div>
      ) : null}
      {status === 'failed' ? (
        <div className="ct-timeline-row ct-timeline-row--failed">
          <span className="ct-tl-x">✕</span>
          <span className="ct-tl-desc ct-tl-desc--failed">La tarea falló</span>
        </div>
      ) : null}
    </div>
  );
}

function renderWithSlashTokens(text: string) {
  const parts = text.split(/((?:^|\s)(\/[a-z]\w*))/gi);
  return parts.map((part, i) => {
    const trimmed = part.trimStart();
    if (/^\/[a-z]\w*/i.test(trimmed)) {
      const isGoal = /^\/goal\b/i.test(trimmed);
      return (
        <span key={i} className={`ct-slash-cmd${isGoal ? ' ct-slash-cmd--goal' : ''}`}>
          {trimmed}
        </span>
      );
    }
    return part;
  });
}

function MessageBubble({ message, index }: { message: ChatMessage; index: number }) {
  const isUser = message.role === 'user';
  const isGoalCmd = isUser && /^\s*\/goal\b/i.test(message.content ?? '');

  return (
    <motion.div
      className={`ct-row ${isUser ? 'ct-row--user' : 'ct-row--agent'}`}
      initial={{ opacity: 0, y: 16, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: 'spring', stiffness: 340, damping: 28, delay: Math.min(index * 0.02, 0.1) }}
    >
      {!isUser ? <div className="ct-avatar" /> : null}

      <div className={`ct-bubble ${isUser ? 'ct-bubble--user' : 'ct-bubble--agent'}`}>
        {isGoalCmd ? <span className="ct-goal-badge">Goal Mode</span> : null}

        {!isUser && !message.content && (message.status === 'sending' || message.status === 'streaming') ? (
          <TypingDots />
        ) : (
          <p className="ct-bubble-text">{renderWithSlashTokens(message.content ?? '')}</p>
        )}

        {message.attachments?.length ? (
          <div className="ct-attachments">
            {message.attachments.map((attachment) => (
              <div key={attachment.id} className="ct-attachment">
                {attachment.dataUrl ? (
                  <img src={attachment.dataUrl} alt={attachment.name} />
                ) : (
                  <span>{attachment.name}</span>
                )}
              </div>
            ))}
          </div>
        ) : null}

        {!isUser && message.taskId ? (
          <TaskTimeline steps={message.taskSteps ?? []} status={message.taskStatus} />
        ) : null}

        <span className="ct-bubble-time">
          {new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </span>
      </div>
    </motion.div>
  );
}

const SUGGESTIONS = [
  { text: 'Captura pantalla', icon: '⬚' },
  { text: '¿Qué puedes hacer por mí?', icon: '✦' },
  { text: 'Mueve el mouse a x=500, y=300', icon: '⊹' },
  { text: 'Ayúdame con una tarea en mi PC', icon: '◈' },
];

const suggestionContainer = {
  hidden: {},
  show: { transition: { staggerChildren: 0.07, delayChildren: 0.15 } },
};
const suggestionItem = {
  hidden: { opacity: 0, y: 14, scale: 0.96 },
  show:   { opacity: 1, y: 0,  scale: 1, transition: { type: 'spring', stiffness: 300, damping: 24 } },
};

function EmptyState({ onSuggest }: { onSuggest: (text: string) => void }) {
  let profileName = '';
  try {
    profileName = JSON.parse(localStorage.getItem('AgentMax.developerProfile') || '{}').displayName || '';
  } catch {
    profileName = '';
  }

  return (
    <div className="ct-empty">
      <AgentMaxLogo size="lg" animated />
      <p className="ct-empty-title">
        {profileName ? `Hola, ${profileName}` : '¿En qué puedo ayudarte?'}
      </p>
      <p className="ct-empty-sub">
        Conversa, adjunta una captura o activa ComputerMax para actuar con permiso.
      </p>
      <motion.div
        className="ct-suggestions"
        variants={suggestionContainer}
        initial="hidden"
        animate="show"
      >
        {SUGGESTIONS.map(({ text, icon }) => (
          <motion.button
            key={text}
            type="button"
            className="ct-suggestion"
            variants={suggestionItem}
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.97 }}
            onClick={() => onSuggest(text)}
          >
            <span style={{ marginRight: 6, opacity: 0.5, fontSize: 13 }}>{icon}</span>
            {text}
          </motion.button>
        ))}
      </motion.div>
    </div>
  );
}

export function ChatThread() {
  const { messages, isTyping, sendMessage, stopAI, agentState, aiStatus } = useAgentStore();
  const [input, setInput] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<PromptInputBoxHandle>(null);
  const busy = agentState === 'working' || isTyping;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isTyping]);

  const handleSend = (text: string, attachments: ChatAttachment[], mode: PromptMode) => {
    if ((!text.trim() && attachments.length === 0) || busy) return;
    const message =
      mode === 'think'
        ? `Analiza en profundidad y valida tus supuestos: ${text || 'describe la imagen adjunta'}`
        : mode === 'computer'
          ? `Usa ComputerMax con permiso explícito para: ${text || 'analizar y actuar sobre la imagen adjunta'}`
          : text;
    void sendMessage(message, attachments);
  };

  return (
    <div className="ct-root">
      <div className="ct-list">
        {messages.length === 0 ? (
          <EmptyState
            onSuggest={(text) => {
              setInput(text);
              inputRef.current?.focus();
            }}
          />
        ) : (
          <AnimatePresence initial={false}>
            {messages.map((message, index) => (
              <MessageBubble key={message.id} message={message} index={index} />
            ))}
          </AnimatePresence>
        )}

        {isTyping && messages[messages.length - 1]?.role === 'user' ? (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ type: 'spring', stiffness: 300, damping: 26 }}
          >
            <TypingDots />
          </motion.div>
        ) : null}

        <div ref={bottomRef} />
      </div>

      <div className="ct-input-area">
        <PromptInputBox
          ref={inputRef}
          value={input}
          onValueChange={setInput}
          onSend={handleSend}
          onStop={stopAI}
          isLoading={busy}
          supportsVision={aiStatus?.supports_vision !== false}
          placeholder="Pregunta, adjunta evidencia o activa ComputerMax..."
        />
      </div>
    </div>
  );
}
