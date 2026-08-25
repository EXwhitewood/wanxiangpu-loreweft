import type { HTMLAttributes, ReactNode } from "react";
import clsx from "clsx";

interface WorkspacePaneHeaderProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
  tone?: "document" | "assistant";
}

export default function WorkspacePaneHeader({
  children,
  className,
  tone = "document",
  ...props
}: WorkspacePaneHeaderProps) {
  return (
    <div
      {...props}
      className={clsx(
        "workspace-pane-header flex h-14 min-h-14 shrink-0 items-center",
        tone === "assistant" && "workspace-pane-header--assistant",
        className
      )}
    >
      {children}
    </div>
  );
}
