import { HTMLMotionProps, motion } from "framer-motion";
import { cn } from "@/utils/cn";
import { ReactNode } from "react";

export interface AnimatedCardProps extends HTMLMotionProps<"div"> {
  children: ReactNode;
  className?: string;
  glowColor?: "magic" | "primary" | "none";
}

export function AnimatedCard({
  children,
  className,
  glowColor = "none",
  ...props
}: AnimatedCardProps) {
  const glowClass = {
    magic: "hover:border-pine-600/35",
    primary: "hover:border-pine-600/35",
    none: "hover:border-pine-700/20"
  }[glowColor];

  return (
    <motion.div
      className={cn(
        "rounded-lg border border-pine-900/10 bg-[var(--surface-raised)] p-6 transition-colors duration-150",
        glowClass,
        className
      )}
      {...props}
    >
      {children}
    </motion.div>
  );
}
