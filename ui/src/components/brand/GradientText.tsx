import type { ReactNode } from 'react';
import clsx from 'clsx';
import './brand.css';

interface GradientTextProps {
  children: ReactNode;
  className?: string;
  as?: 'span' | 'h1' | 'h2' | 'p' | 'strong';
  animated?: boolean;
}

export function GradientText({
  children,
  className,
  as: Tag = 'span',
  animated = true,
}: GradientTextProps) {
  return (
    <Tag
      className={clsx(
        'am-brand-script',
        'am-gradient-text',
        !animated && 'am-gradient-text--static',
        className,
      )}
    >
      {children}
    </Tag>
  );
}
