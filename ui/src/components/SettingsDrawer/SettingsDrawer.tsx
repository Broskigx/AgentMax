import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Activity, Cpu, ShieldCheck, X } from 'lucide-react';
import { ExpandableTabs } from '../ui/expandable-tabs';
import { SettingsPanels, type SettingsSection } from './SettingsPanels';
import './SettingsDrawer.css';

interface SettingsDrawerProps {
  open: boolean;
  onClose: () => void;
}

export function SettingsDrawer({ open, onClose }: SettingsDrawerProps) {
  const [section, setSection] = useState<SettingsSection>('models');
  const sections: SettingsSection[] = ['models', 'diagnostics', 'safety'];

  return (
    <AnimatePresence>
      {open ? (
        <>
          <motion.button
            type="button"
            className="settings-drawer__backdrop"
            aria-label="Cerrar configuración"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            onClick={onClose}
          />
          <motion.aside
            className="settings-drawer"
            role="dialog"
            aria-label="Configuración"
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ type: 'spring', stiffness: 320, damping: 32 }}
          >
            <header className="settings-drawer__header">
              <div>
                <span className="settings-drawer__eyebrow">Configuración</span>
                <h2>AgentMax</h2>
              </div>
              <button type="button" className="settings-drawer__close" onClick={onClose} aria-label="Cerrar">
                <X size={18} />
              </button>
            </header>
            <div className="settings-drawer__tabs">
              <ExpandableTabs
                tabs={[
                  { title: 'Modelos', icon: Cpu },
                  { title: 'Diagnóstico', icon: Activity },
                  { title: 'Seguridad', icon: ShieldCheck },
                ]}
                selectedIndex={sections.indexOf(section)}
                onChange={(index) => {
                  if (index !== null) setSection(sections[index]);
                }}
                className="w-full justify-center"
              />
            </div>
            <div className="settings-drawer__body">
              <SettingsPanels active={open} section={section} />
            </div>
          </motion.aside>
        </>
      ) : null}
    </AnimatePresence>
  );
}
