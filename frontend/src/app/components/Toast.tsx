import { useEffect } from "react";
import { X } from "lucide-react";

interface ToastProps {
  readonly message: string;
  readonly onClose: () => void;
  readonly duration?: number;
  readonly variant?: "error" | "success";
}

export default function Toast({
  message,
  onClose,
  duration = 5000,
  variant = "error",
}: Readonly<ToastProps>) {
  useEffect(() => {
    if (duration > 0) {
      const timer = setTimeout(onClose, duration);
      return () => clearTimeout(timer);
    }
  }, [duration, onClose]);

  const tone =
    variant === "success"
      ? {
          container: "bg-emerald-50 border-emerald-200",
          text: "text-emerald-800",
          icon: "text-emerald-400 hover:text-emerald-600",
        }
      : {
          container: "bg-red-50 border-red-200",
          text: "text-red-800",
          icon: "text-red-400 hover:text-red-600",
        };

  return (
    <div className={`fixed top-4 right-4 z-50 border rounded-lg p-4 shadow-lg max-w-md flex items-start gap-3 ${tone.container}`}>
      <div className="flex-1">
        <p className={`text-sm ${tone.text}`}>{message}</p>
      </div>
      <button onClick={onClose} className={tone.icon}>
        <X size={16} />
      </button>
    </div>
  );
}
