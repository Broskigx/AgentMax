import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface RealismButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  text?: string;
  children?: ReactNode;
}

export default function RealismButton({
  text,
  children,
  className,
  disabled,
  type = 'button',
  ...props
}: RealismButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled}
      className={cn(
        'group relative isolate cursor-pointer rounded-2xl border-0 bg-[radial-gradient(circle_90px_at_82%_-16%,#fff,#3a2419_42%,#171717_76%)] p-[2px] text-sm font-semibold text-white transition-all duration-300',
        'shadow-[0_12px_34px_rgba(0,0,0,0.34)] hover:-translate-y-0.5 hover:shadow-[0_18px_44px_rgba(249,115,22,0.22)]',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-orange-400',
        'disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:translate-y-0',
        className,
      )}
      {...props}
    >
      <span className="pointer-events-none absolute right-0 top-0 -z-10 h-[64%] w-[68%] rounded-[120px] shadow-[0_0_22px_rgba(255,255,255,0.18)] transition-all duration-300 group-hover:shadow-[0_0_42px_rgba(255,255,255,0.3)]" />
      <span className="pointer-events-none absolute bottom-0 left-0 h-[54%] w-[54px] rounded-2xl bg-[radial-gradient(circle_64px_at_0%_100%,#fb923c,#f9731680,transparent)] shadow-[-2px_9px_38px_rgba(249,115,22,0.28)] transition-all duration-300 group-hover:w-[96px] group-hover:shadow-[-4px_2px_46px_rgba(249,115,22,0.42)]" />
      <span className="relative z-10 flex min-h-11 items-center justify-center gap-2 rounded-[14px] bg-[radial-gradient(circle_96px_at_82%_-55%,#666,#151515_58%,#0c0c0c)] px-6 py-3 transition-transform duration-300 group-hover:scale-[1.025]">
        {children ?? text}
        <span className="pointer-events-none absolute inset-0 -z-10 rounded-[14px] bg-[radial-gradient(circle_72px_at_0%_100%,rgba(249,115,22,0.2),rgba(234,88,12,0.06),transparent)]" />
      </span>
    </button>
  );
}
