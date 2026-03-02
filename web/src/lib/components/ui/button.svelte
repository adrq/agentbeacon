<script lang="ts">
  import type { Snippet } from 'svelte';
  import type { HTMLButtonAttributes } from 'svelte/elements';

  interface Props extends HTMLButtonAttributes {
    variant?: 'default' | 'secondary' | 'outline' | 'destructive' | 'ghost';
    size?: 'sm' | 'md' | 'lg';
    children?: Snippet;
  }

  let { variant = 'default', size = 'md', disabled = false, type = 'button', children, class: className, ...restProps }: Props = $props();

  const baseClass = 'inline-flex items-center justify-center gap-1 rounded-md font-medium transition outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:opacity-60 disabled:cursor-not-allowed';

  const variants = {
    default: 'bg-primary text-primary-foreground hover:brightness-110',
    secondary: 'bg-secondary text-secondary-foreground hover:bg-secondary/70',
    outline: 'border border-border hover:bg-accent/30',
    destructive: 'bg-destructive text-destructive-foreground hover:brightness-110',
    ghost: 'hover:bg-accent/30'
  };

  const sizes = {
    sm: 'h-7 px-2 text-[0.6875rem]',
    md: 'h-9 px-3 text-sm',
    lg: 'h-11 px-5 text-[0.875rem]'
  };
</script>

<button
  {type}
  {disabled}
  {...restProps}
  class="{baseClass} {variants[variant]} {sizes[size]} {className || ''}"
>
  {#if children}{@render children()}{/if}
</button>

<style>
  button { -webkit-tap-highlight-color: transparent; }
</style>
