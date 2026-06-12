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
        {[0, 1, 2].map((index) => (
          <motion.span
            key={index}
            className="ct-dot"
            animate={{ y: [0, -5, 0], opacity: [0.4, 1, 0.4] }}
            transition={{ duration: 0.8, repeat: Infinity, delay: index * 0.15 }}
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
          <div key={`${step.stepNumber}-${step.timestamp}`} className="ct-timeline-row">
            <span className={`ct-tl-dot ${isRunning ? 'ct-tl-dot--pulse' : 'ct-tl-dot--done'}`} />
            <span className="ct-tl-num">{step.stepNumber}</span>
            <span className="ct-tl-desc">{step.description}</span>
          </div>
        );
      })}
      {status === 'completed' ? (
        <div className="ct-timeline-row ct-timeline-row--done">
          <span className="ct-tl-check">OK</span>
          <span className="ct-tl-desc ct-tl-desc--done">Tarea completada</span>
        </div>
      ) : null}
      {status === 'failed' ? (
        <div className="ct-timeline-row ct-timeline-row--failed">
          <span className="ct-tl-x">X</span>
          <span className="ct-tl-desc ct-tl-desc--failed">La tarea fallo</span>
        </div>
      ) : null}
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user';

  return (
    <motion.div
      className={`ct-row ${isUser ? 'ct-row--user' : 'ct-row--agent'}`}
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: 'spring', stiffness: 300, damping: 28 }}
    >
      {!isUser ? <div className="ct-avatar" /> : null}

      <div className={`ct-bubble ${isUser ? 'ct-bubble--user' : 'ct-bubble--agent'}`}>
        {!isUser && !message.content && (message.status === 'sending' || message.status === 'streaming') ? (
          <TypingDots />
        ) : (
          <p className="ct-bubble-text">{message.content}</p>
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
  'Mueve el mouse a x=500, y=300',
  'Captura pantalla',
  'Que puedes hacer por mi?',
  'Ayudame con una tarea en mi PC',
];

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
      <p className="ct-empty-title">{profileName ? `Hola, ${profileName}` : 'En que puedo ayudarte?'}</p>
      <p className="ct-empty-sub">Conversa, adjunta una captura o activa ComputerMax para actuar con permiso.</p>
      <div className="ct-suggestions">
        {SUGGESTIONS.map((suggestion) => (
          <button
            key={suggestion}
            type="button"
            className="ct-suggestion"
            onClick={() => onSuggest(suggestion)}
          >
            {suggestion}
          </button>
        ))}
      </div>
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
          ? `Usa ComputerMax con permiso explicito para: ${text || 'analizar y actuar sobre la imagen adjunta'}`
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
            {messages.map((message) => (
              <MessageBubble key={message.id} message={message} />
            ))}
          </AnimatePresence>
        )}

        {isTyping && messages[messages.length - 1]?.role === 'user' ? (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
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
