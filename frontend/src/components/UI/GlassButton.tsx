import { HTMLMotionProps, motion } from "framer-motion";
import { cn } from "@/utils/cn";
import { ReactNode } from "react";

export interface GlassButtonProps extends HTMLMotionProps<"button"> {
  children: ReactNode;
  variant?: "primary" | "secondary" | "danger" | "ghost";
  className?: string;
}

export function GlassButton({
  children,
  variant = "secondary",
  className,
  ...props
}: GlassButtonProps) {
  const baseClasses = "glass-button relative flex h-10 items-center justify-center gap-2 whitespace-nowrap rounded-md px-3.5 text-sm font-semibold outline-none transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-pine-600/35 focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-50";
  
  const variants = {
    primary: "bg-pine-700 text-white hover:bg-pine-800 active:bg-pine-900",
    secondary: "border border-pine-900/15 bg-[var(--surface-raised)] text-pine-800 hover:border-pine-700/30 hover:bg-pine-50",
    danger: "border border-red-200 bg-red-50 text-red-700 hover:border-red-300 hover:bg-red-100",
    ghost: "text-pine-700 hover:bg-pine-900/[0.05] hover:text-pine-900",
  };

  return (
    <motion.button
      data-variant={variant}
      className={cn(baseClasses, variants[variant], className)}
      {...props}
    >
      {children}
    </motion.button>
  );
}
