import type { ReactNode } from 'react';

type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost';
type ButtonType = 'button' | 'submit';

interface ButtonProps {
  children: ReactNode;
  variant?: ButtonVariant;
  disabled?: boolean;
  onClick?: () => void;
  type?: ButtonType;
}

export function Button({
  children,
  variant = 'secondary',
  disabled,
  onClick,
  type = 'button',
}: ButtonProps) {
  const styles = {
    primary: 'border-emerald-400/30 bg-emerald-400/15 text-emerald-200 hover:bg-emerald-400/25',
    secondary: 'border-white/10 bg-white/[0.05] text-text hover:bg-white/[0.09]',
    danger: 'border-rose-400/30 bg-rose-400/10 text-rose-200 hover:bg-rose-400/20',
    ghost: 'border-transparent bg-transparent text-text-muted hover:bg-white/[0.06] hover:text-text',
  }[variant];

  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className={`inline-flex min-h-10 items-center justify-center gap-2 rounded-xl border px-3 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${styles}`}
    >
      {children}
    </button>
  );
}
