import { MessageSquarePlus, Settings2, Trash2 } from 'lucide-react';
import { useAgentStore } from '../../store/agentStore';
import './ChatSidebar.css';

interface ChatSidebarProps {
  onOpenSettings: () => void;
}

export function ChatSidebar({ onOpenSettings }: ChatSidebarProps) {
  const chatSessions = useAgentStore((s) => s.chatSessions);
  const activeChatId = useAgentStore((s) => s.activeChatId);
  const createNewChat = useAgentStore((s) => s.createNewChat);
  const switchChat = useAgentStore((s) => s.switchChat);
  const deleteChat = useAgentStore((s) => s.deleteChat);

  return (
    <aside className="chat-sidebar" aria-label="Chats">
      <div className="chat-sidebar__head">
        <span>Chats</span>
        <button type="button" className="chat-sidebar__new" onClick={createNewChat} title="Nuevo chat">
          <MessageSquarePlus size={15} strokeWidth={1.75} />
        </button>
      </div>

      <div className="chat-sidebar__list">
        {chatSessions.map((session) => {
          const isActive = session.id === activeChatId;
          return (
            <div
              key={session.id}
              className={`chat-sidebar__item ${isActive ? 'chat-sidebar__item--active' : ''}`}
            >
              <button
                type="button"
                className="chat-sidebar__item-btn"
                onClick={() => switchChat(session.id)}
              >
                <span className="chat-sidebar__item-title">{session.title}</span>
                <span className="chat-sidebar__item-preview">{session.preview}</span>
              </button>
              {chatSessions.length > 1 ? (
                <button
                  type="button"
                  className="chat-sidebar__item-delete"
                  onClick={() => deleteChat(session.id)}
                  aria-label="Eliminar chat"
                >
                  <Trash2 size={12} />
                </button>
              ) : null}
            </div>
          );
        })}
      </div>

      <button type="button" className="chat-sidebar__settings" onClick={onOpenSettings}>
        <Settings2 size={15} strokeWidth={1.75} />
        Configuración
      </button>
    </aside>
  );
}
