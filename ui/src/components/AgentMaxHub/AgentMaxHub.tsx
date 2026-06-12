import { AnimatePresence, motion } from 'framer-motion';
import { MessageSquarePlus, MessagesSquare, Settings2, Trash2, X } from 'lucide-react';
import { useAgentStore } from '../../store/agentStore';
import { SettingsPanels } from '../SettingsDrawer/SettingsPanels';
import '../SettingsDrawer/SettingsDrawer.css';
import './AgentMaxHub.css';

interface AgentMaxHubProps {
  open: boolean;
  onClose: () => void;
}

export function AgentMaxHub({ open, onClose }: AgentMaxHubProps) {
  const chatSessions = useAgentStore((s) => s.chatSessions);
  const activeChatId = useAgentStore((s) => s.activeChatId);
  const createNewChat = useAgentStore((s) => s.createNewChat);
  const switchChat = useAgentStore((s) => s.switchChat);
  const deleteChat = useAgentStore((s) => s.deleteChat);

  return (
    <AnimatePresence>
      {open ? (
        <>
          <motion.button
            type="button"
            className="agentmax-hub__backdrop"
            aria-label="Cerrar panel"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            onClick={onClose}
          />
          <motion.aside
            className="agentmax-hub"
            role="dialog"
            aria-label="Panel AgentMax"
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ type: 'spring', stiffness: 320, damping: 34 }}
          >
            <header className="agentmax-hub__header">
              <div>
                <span className="agentmax-hub__eyebrow">AgentMax Hub</span>
                <h2>Chats y configuración</h2>
              </div>
              <button type="button" className="agentmax-hub__close" onClick={onClose} aria-label="Cerrar">
                <X size={18} />
              </button>
            </header>

            <div className="agentmax-hub__body">
              <section className="agentmax-hub__section">
                <div className="agentmax-hub__section-head">
                  <MessagesSquare size={15} />
                  <span>Chats</span>
                  <button
                    type="button"
                    className="agentmax-hub__new-chat"
                    onClick={() => {
                      createNewChat();
                      onClose();
                    }}
                  >
                    <MessageSquarePlus size={14} />
                    Nuevo
                  </button>
                </div>

                <div className="agentmax-hub__chat-list">
                  {chatSessions.map((session) => {
                    const isActive = session.id === activeChatId;
                    return (
                      <div
                        key={session.id}
                        className={`agentmax-hub__chat-item ${isActive ? 'agentmax-hub__chat-item--active' : ''}`}
                      >
                        <button
                          type="button"
                          className="agentmax-hub__chat-main"
                          onClick={() => {
                            switchChat(session.id);
                            onClose();
                          }}
                        >
                          <strong>{session.title}</strong>
                          <span>{session.preview}</span>
                          <time>
                            {new Date(session.updatedAt).toLocaleString('es-ES', {
                              day: '2-digit',
                              month: 'short',
                              hour: '2-digit',
                              minute: '2-digit',
                            })}
                          </time>
                        </button>
                        {chatSessions.length > 1 ? (
                          <button
                            type="button"
                            className="agentmax-hub__chat-delete"
                            aria-label="Eliminar chat"
                            onClick={() => deleteChat(session.id)}
                          >
                            <Trash2 size={13} />
                          </button>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              </section>

              <section className="agentmax-hub__section agentmax-hub__section--config">
                <div className="agentmax-hub__section-head">
                  <Settings2 size={15} />
                  <span>Configuración</span>
                </div>
                <div className="agentmax-hub__config">
                  <SettingsPanels active={open} />
                </div>
              </section>
            </div>
          </motion.aside>
        </>
      ) : null}
    </AnimatePresence>
  );
}
