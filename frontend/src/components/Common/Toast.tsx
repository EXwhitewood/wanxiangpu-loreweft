import { useEffect, useState } from "react";
import { Check, AlertTriangle, X } from "lucide-react";
import clsx from "clsx";

interface ToastProps {
  type: "success" | "error" | "warning";
  message: string;
  onClose: () => void;
}

export default function Toast({ type, message, onClose }: ToastProps) {
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    const timer = setTimeout(() => {
      setVisible(false);
      setTimeout(onClose, 300);
    }, 3000);
    return () => clearTimeout(timer);
  }, [onClose]);

  const icons = {
    success: <Check className="h-4 w-4 text-jade-400" />,
    error: <X className="h-4 w-4 text-crimson-400" />,
    warning: <AlertTriangle className="h-4 w-4 text-magic-400" />,
  };

  const borderColors = {
    success: "border-jade-400/30",
    error: "border-crimson-400/30",
    warning: "border-magic-400/30",
  };

  const bgColors = {
    success: "bg-jade-400/10",
    error: "bg-crimson-400/10",
    warning: "bg-magic-400/10",
  };

  return (
    <div
      className={clsx(
        "app-toast fixed right-6 top-16 z-50 flex items-center gap-2 rounded-lg border px-4 py-3 shadow-lg transition-all duration-300",
        borderColors[type],
        bgColors[type],
        visible ? "translate-x-0 opacity-100" : "translate-x-4 opacity-0"
      )}
    >
      {icons[type]}
      <span className="text-sm text-pine-700">{message}</span>
      <button
        onClick={() => {
          setVisible(false);
          setTimeout(onClose, 300);
        }}
        className="ml-2 text-pine-700 hover:text-pine-700"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
