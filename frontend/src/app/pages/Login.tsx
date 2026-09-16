import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Database, GitBranch, ShieldCheck, FileSearch } from "lucide-react";

import { BRANDING } from "../config/branding";
import { login } from "../utils/userIdentity";
import { sttmNav } from "../utils/sttmRoutes";
import sttmLogo from "../assets/sttm-logo.svg";
import Button from "../components/ui/Button";

export default function Login() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");

  const handleSubmit: React.ComponentProps<"form">["onSubmit"] = (e) => {
    e.preventDefault();
    if (!name.trim()) {
      setError("Please enter your name to continue.");
      return;
    }
    if (email.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) {
      setError("Please enter a valid email address.");
      return;
    }
    login(name, email);
    navigate(sttmNav("/dashboard"), { replace: true });
  };

  return (
    <div className="min-h-screen w-full flex bg-brand-light">
      {/* Brand panel */}
      <div className="hidden lg:flex flex-col justify-between w-[42%] bg-brand-darkblue text-white p-12 relative overflow-hidden">
        <div className="absolute -top-24 -right-24 w-96 h-96 rounded-full bg-brand-primary/20 blur-3xl" />
        <div className="absolute -bottom-32 -left-16 w-96 h-96 rounded-full bg-brand-primary/10 blur-3xl" />

        <div className="relative z-10 flex items-center gap-3">
          <div className="bg-white rounded-lg p-1.5">
            <img src={sttmLogo} alt={BRANDING.APP_NAME} className="h-8 w-8" />
          </div>
          <span className="text-2xl font-extrabold tracking-tight">{BRANDING.APP_NAME}</span>
        </div>

        <div className="relative z-10">
          <h1 className="text-4xl font-extrabold leading-tight">
            Source‑to‑Target Mapping,<br />
            <span className="text-brand-primary">accelerated by AI.</span>
          </h1>
          <p className="mt-4 text-white/70 max-w-md">
            Profile data, generate mappings, and validate requirements — all in one
            intelligent workspace.
          </p>

          <ul className="mt-8 space-y-4 text-sm">
            <li className="flex items-center gap-3"><Database size={18} className="text-brand-primary" /> Automated data profiling & quality scoring</li>
            <li className="flex items-center gap-3"><GitBranch size={18} className="text-brand-primary" /> AI‑assisted source‑to‑target mapping</li>
            <li className="flex items-center gap-3"><FileSearch size={18} className="text-brand-primary" /> Requirement extraction from BRDs</li>
            <li className="flex items-center gap-3"><ShieldCheck size={18} className="text-brand-primary" /> Human‑in‑the‑loop review & approval</li>
          </ul>
        </div>

        <div className="relative z-10 text-xs text-white/50">© {2026} STTM. All rights reserved.</div>
      </div>

      {/* Form panel */}
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="w-full max-w-md">
          {/* Mobile logo */}
          <div className="lg:hidden flex items-center gap-3 mb-8 justify-center">
            <img src={sttmLogo} alt={BRANDING.APP_NAME} className="h-9 w-9" />
            <span className="text-2xl font-extrabold text-brand-darkblue">{BRANDING.APP_NAME}</span>
          </div>

          <div className="bg-white rounded-2xl shadow-xl border border-gray-100 p-8">
            <h2 className="text-2xl font-bold text-brand-darkblue">Welcome back</h2>
            <p className="mt-1 text-sm text-gray-500">Sign in to continue to {BRANDING.APP_NAME}.</p>

            <form onSubmit={handleSubmit} className="mt-7 space-y-5">
              <div>
                <label htmlFor="login-name" className="block text-sm font-medium text-gray-700 mb-1.5">
                  Name <span className="text-brand-error">*</span>
                </label>
                <input
                  id="login-name"
                  type="text"
                  value={name}
                  autoFocus
                  onChange={(e) => { setName(e.target.value); setError(""); }}
                  placeholder="e.g. Alex Morgan"
                  className="w-full px-4 py-2.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary/40 focus:border-brand-primary"
                />
              </div>

              <div>
                <label htmlFor="login-email" className="block text-sm font-medium text-gray-700 mb-1.5">
                  Email <span className="text-gray-400 font-normal">(optional)</span>
                </label>
                <input
                  id="login-email"
                  type="email"
                  value={email}
                  onChange={(e) => { setEmail(e.target.value); setError(""); }}
                  placeholder="you@company.com"
                  className="w-full px-4 py-2.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary/40 focus:border-brand-primary"
                />
              </div>

              {error && <p className="text-sm text-brand-error">{error}</p>}

              <Button type="submit" size="lg" fullWidth rightIcon={<ArrowRight size={18} />}>
                Sign in
              </Button>
            </form>

            <p className="mt-6 text-xs text-center text-gray-400">
              This is a local workspace — your name identifies your sessions on this device.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
