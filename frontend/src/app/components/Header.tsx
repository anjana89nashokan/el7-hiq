import { useNavigate } from "react-router-dom";
import { Home } from "lucide-react";

import { sttmNav } from "../utils/sttmRoutes";
import ustHealthIqLogo from "../../assets/ust-healthiq-white.jpg";

export default function Header() {
  const navigate = useNavigate();

  return (
    <header className="bg-white px-6 py-3 flex items-center justify-between border-b border-gray-200">
      <div className="flex items-center">
        <img
          src={ustHealthIqLogo}
          alt="UST HealthIQ"
          className="h-9 w-auto object-contain"
        />
      </div>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => navigate(sttmNav("/dashboard"))}
          className="p-2 text-brand-darkblue hover:bg-brand-surface rounded-lg transition-colors"
          title="Go to Dashboard"
        >
          <Home className="w-5 h-5" />
        </button>
      </div>
    </header>
  );
}
