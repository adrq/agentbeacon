<script lang="ts">
  import { cn } from '../../utils/cn';
  export let variant: 'default' | 'secondary' | 'outline' | 'destructive' | 'ghost' = 'default';
  export let size: 'sm' | 'md' | 'lg' = 'md';
  export let disabled = false;
  export let type: 'button' | 'submit' = 'button';
  export let className = '';

  const variantClasses: Record<string,string> = {
    default: 'bg-primary text-primary-foreground hover:brightness-110',
    secondary: 'bg-secondary text-secondary-foreground hover:bg-secondary/70',
    outline: 'border border-border hover:bg-accent/30',
    destructive: 'bg-destructive text-destructive-foreground hover:brightness-110',
    ghost: 'hover:bg-accent/30'
  };
  const sizeClasses: Record<string,string> = {
    sm: 'h-7 px-2 text-xs',
    md: 'h-9 px-3 text-sm',
    lg: 'h-11 px-5 text-base'
  };
</script>

<button
  {type}
  {disabled}
  {...$$restProps}
  class={cn('inline-flex items-center justify-center gap-1 rounded-md font-medium transition outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:opacity-60 disabled:cursor-not-allowed', variantClasses[variant], sizeClasses[size], className, $$restProps.class)}
><slot /></button>

<style>
  button { -webkit-tap-highlight-color: transparent; }
</style>
