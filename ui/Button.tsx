import React, { type ReactNode } from 'react';

type ButtonVariant = 'primary' | 'positive' | 'secondary' | 'danger' | 'ghost';

type ButtonProps = {
  children: ReactNode;
  icon?: ReactNode;
  iconOnly?: boolean;
  variant?: ButtonVariant;
  disabled?: boolean;
  onClick?: (event: React.MouseEvent<HTMLButtonElement>) => void;
  type?: 'button' | 'submit';
  ariaLabel?: string;
  testId?: string;
};

const styles: Record<ButtonVariant, string> = {
  primary: 'border-border bg-surface text-accent hover:border-border-subtle hover:bg-accent-subtle active:bg-accent/20 focus-visible:ring-accent/60',
  positive: 'border-border bg-surface text-positive hover:border-border-subtle hover:bg-positive-subtle active:bg-positive/20 focus-visible:ring-positive/60',
  secondary: 'border-border bg-surface text-text hover:border-border-subtle hover:bg-surface-raised active:bg-surface focus-visible:ring-border-subtle',
  danger: 'border-border bg-surface text-negative hover:border-border-subtle hover:bg-negative-subtle active:bg-negative/20 focus-visible:ring-negative/60',
  ghost: 'border-transparent bg-transparent text-text-muted hover:bg-surface hover:text-text active:bg-surface-raised focus-visible:ring-border',
};

export function Button({ children, icon, iconOnly = false, variant = 'secondary', disabled = false, onClick, type = 'button', ariaLabel, testId }: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      aria-label={ariaLabel}
      data-testid={testId}
      className={`inline-flex min-h-11 min-w-11 items-center justify-center gap-1.5 rounded-[var(--control-radius)] border px-2.5 text-xs font-medium transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-45 ${iconOnly ? 'px-0' : ''} ${styles[variant]}`}
    >
      {icon ? <span aria-hidden="true" className="shrink-0">{icon}</span> : null}
      {children}
    </button>
  );
}
