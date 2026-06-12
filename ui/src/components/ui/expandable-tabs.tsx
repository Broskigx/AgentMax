import * as React from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { useOnClickOutside } from 'usehooks-ts';
import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

interface Tab {
  title: string;
  icon: LucideIcon;
  type?: never;
}

interface Separator {
  type: 'separator';
  title?: never;
  icon?: never;
}

export type ExpandableTabItem = Tab | Separator;

interface ExpandableTabsProps {
  tabs: ExpandableTabItem[];
  className?: string;
  activeColor?: string;
  selectedIndex?: number | null;
  collapseOnOutsideClick?: boolean;
  onChange?: (index: number | null) => void;
}

const buttonVariants = {
  initial: { gap: 0, paddingLeft: '0.625rem', paddingRight: '0.625rem' },
  animate: (isSelected: boolean) => ({
    gap: isSelected ? '0.5rem' : 0,
    paddingLeft: isSelected ? '0.875rem' : '0.625rem',
    paddingRight: isSelected ? '0.875rem' : '0.625rem',
  }),
};

const spanVariants = {
  initial: { width: 0, opacity: 0 },
  animate: { width: 'auto', opacity: 1 },
  exit: { width: 0, opacity: 0 },
};

const transition = { type: 'spring', bounce: 0, duration: 0.45 } as const;

export function ExpandableTabs({
  tabs,
  className,
  activeColor = 'text-orange-300',
  selectedIndex,
  collapseOnOutsideClick = false,
  onChange,
}: ExpandableTabsProps) {
  const [internalSelected, setInternalSelected] = React.useState<number | null>(selectedIndex ?? null);
  const outsideClickRef = React.useRef<HTMLDivElement>(null);
  const selected = selectedIndex === undefined ? internalSelected : selectedIndex;

  React.useEffect(() => {
    if (selectedIndex !== undefined) setInternalSelected(selectedIndex);
  }, [selectedIndex]);

  useOnClickOutside(outsideClickRef, () => {
    if (!collapseOnOutsideClick) return;
    setInternalSelected(null);
    onChange?.(null);
  });

  const handleSelect = (index: number) => {
    if (selectedIndex === undefined) setInternalSelected(index);
    onChange?.(index);
  };

  return (
    <div
      ref={outsideClickRef}
      className={cn(
        'flex min-w-0 items-center gap-1 rounded-xl border border-white/[0.08] bg-black/35 p-1 shadow-sm backdrop-blur-xl',
        className,
      )}
    >
      {tabs.map((tab, index) => {
        if (tab.type === 'separator') {
          return <div key={`separator-${index}`} className="mx-1 h-6 w-px bg-white/10" aria-hidden="true" />;
        }

        const Icon = tab.icon;
        const isSelected = selected === index;
        return (
          <motion.button
            key={tab.title}
            type="button"
            variants={buttonVariants}
            initial={false}
            animate="animate"
            custom={isSelected}
            onClick={() => handleSelect(index)}
            transition={transition}
            aria-pressed={isSelected}
            aria-label={tab.title}
            className={cn(
              'relative flex h-9 min-w-9 items-center justify-center rounded-lg border border-transparent text-xs font-semibold transition-colors duration-200',
              isSelected
                ? cn('border-orange-400/20 bg-orange-400/10', activeColor)
                : 'text-zinc-500 hover:bg-white/[0.05] hover:text-zinc-200',
            )}
          >
            <Icon size={16} strokeWidth={1.8} />
            <AnimatePresence initial={false}>
              {isSelected && (
                <motion.span
                  variants={spanVariants}
                  initial="initial"
                  animate="animate"
                  exit="exit"
                  transition={transition}
                  className="overflow-hidden whitespace-nowrap"
                >
                  {tab.title}
                </motion.span>
              )}
            </AnimatePresence>
          </motion.button>
        );
      })}
    </div>
  );
}
