import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { TitleBar } from '../TitleBar/TitleBar';
import { ChatSidebar } from '../ChatSidebar/ChatSidebar';
import { ChatThread } from '../ChatThread/ChatThread';
import { SettingsDrawer } from '../SettingsDrawer/SettingsDrawer';
import { NoModelAlert } from '../NoModelAlert/NoModelAlert';
import { useAgentStore } from '../../store/agentStore';
import './MainWindow.css';

export function MainWindow() {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const refreshConnectionStatus = useAgentStore((s) => s.refreshConnectionStatus);
  const fetchLMStudioModels = useAgentStore((s) => s.fetchLMStudioModels);
  const fetchAIStatus = useAgentStore((s) => s.fetchAIStatus);

  useEffect(() => {
    void refreshConnectionStatus();
    void fetchAIStatus();
    void fetchLMStudioModels();
    const id = window.setInterval(() => {
      void refreshConnectionStatus();
      void fetchAIStatus();
      void fetchLMStudioModels();
    }, 14000);
    return () => window.clearInterval(id);
  }, [refreshConnectionStatus, fetchAIStatus, fetchLMStudioModels]);

  return (
    <motion.main
      className="agentmax-shell"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.22 }}
    >
      <TitleBar />

      <div className="agentmax-shell__body">
        <ChatSidebar onOpenSettings={() => setSettingsOpen(true)} />
        <section className="agentmax-shell__chat">
          <ChatThread />
        </section>
      </div>

      <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      <NoModelAlert />
    </motion.main>
  );
}
