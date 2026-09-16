import { useEffect, useState } from "react";
import { KeyRound, Eye, EyeOff, CheckCircle2, Loader2, AlertCircle } from "lucide-react";
import {
  getLlmSettings,
  saveLlmSettings,
  type LlmProvider,
  type LlmSettings,
} from "../end-points/settingsApi";

type Status = { type: "ok" | "error"; msg: string } | null;

const PROVIDER_LABELS: Record<LlmProvider, string> = {
  gemini: "Google Gemini",
  groq: "Groq",
  openai: "ChatGPT (OpenAI)",
};

const KEY_URLS: Record<LlmProvider, string> = {
  gemini: "https://aistudio.google.com/apikey",
  groq: "https://console.groq.com/keys",
  openai: "https://platform.openai.com/api-keys",
};

const KEY_PLACEHOLDERS: Record<LlmProvider, string> = {
  gemini: "Paste your Gemini API key (AIza…)",
  groq: "Paste your Groq API key (gsk_…)",
  openai: "Paste your OpenAI API key (sk-…)",
};

function isProvider(value: string): value is LlmProvider {
  return value === "gemini" || value === "groq" || value === "openai";
}

export default function Settings() {
  const [settings, setSettings] = useState<LlmSettings | null>(null);
  const [provider, setProvider] = useState<LlmProvider>("gemini");
  const [keyInput, setKeyInput] = useState("");
  const [modelInput, setModelInput] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<Status>(null);

  const refresh = async () => {
    try {
      const data = await getLlmSettings();
      setSettings(data);
      if (isProvider(data.provider)) {
        setProvider(data.provider);
      }
      if (data.provider === "openai") {
        setModelInput(data.openai?.model || "gpt-4o-mini");
      } else if (data.provider === "groq") {
        setModelInput(data.groq?.model || "");
      } else {
        setModelInput("");
      }
    } catch {
      setStatus({ type: "error", msg: "Could not load current settings." });
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const current = settings?.[provider];
  const placeholder = current?.configured
    ? `Currently set: ${current.masked} — paste a new key to replace it`
    : KEY_PLACEHOLDERS[provider];

  const handleProviderChange = (next: LlmProvider) => {
    setProvider(next);
    setStatus(null);
    setKeyInput("");
    if (next === "openai") {
      setModelInput(settings?.openai?.model || "gpt-4o-mini");
    } else if (next === "groq") {
      setModelInput(settings?.groq?.model || "");
    } else {
      setModelInput("");
    }
  };

  const handleSave = async () => {
    setStatus(null);
    const key = keyInput.trim();
    if (!key && !settings?.[provider]?.configured) {
      setStatus({
        type: "error",
        msg: `Enter a ${PROVIDER_LABELS[provider]} API key to save.`,
      });
      return;
    }

    setSaving(true);
    try {
      const body: {
        provider: LlmProvider;
        google_api_key?: string;
        groq_api_key?: string;
        groq_model?: string;
        openai_api_key?: string;
        openai_model?: string;
      } = { provider };

      if (key) {
        if (provider === "gemini") body.google_api_key = key;
        else if (provider === "groq") body.groq_api_key = key;
        else body.openai_api_key = key;
      }

      if (provider === "groq" && modelInput.trim()) {
        body.groq_model = modelInput.trim();
      }
      if (provider === "openai" && modelInput.trim()) {
        body.openai_model = modelInput.trim();
      }

      const data = await saveLlmSettings(body);
      setSettings(data);
      setKeyInput("");
      setStatus({ type: "ok", msg: data.message || "Saved. The app now runs on your key." });
    } catch (e) {
      setStatus({ type: "error", msg: e instanceof Error ? e.message : "Failed to save." });
    } finally {
      setSaving(false);
    }
  };

  const keyUrl = KEY_URLS[provider];

  return (
    <div className="min-h-screen bg-brand-light text-slate-900 font-sans pb-12">
      <main className="max-w-2xl mx-auto p-6 lg:p-8">
        <div className="flex items-center gap-3 mb-2">
          <KeyRound size={24} className="text-brand-darkblue" strokeWidth={1.5} />
          <h1 className="text-2xl font-bold text-brand-darkblue">Settings</h1>
        </div>
        <p className="text-slate-500 mb-6">Bring your own LLM key — the app runs on the key you provide here.</p>

        <section className="bg-white border border-gray-200 rounded-xl p-6 shadow-sm">
          <h2 className="text-lg font-semibold text-brand-darkblue mb-4">LLM Provider &amp; API Key</h2>

          <label htmlFor="provider" className="block text-sm font-medium text-slate-700 mb-1">
            Provider
          </label>
          <select
            id="provider"
            value={provider}
            onChange={(e) => handleProviderChange(e.target.value as LlmProvider)}
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm mb-1 focus:outline-none focus:ring-2 focus:ring-brand-primary/40"
          >
            <option value="gemini">Google Gemini</option>
            <option value="openai">ChatGPT (OpenAI)</option>
            <option value="groq">Groq</option>
          </select>
          <p className="text-xs text-slate-500 mb-4">
            {current?.configured ? (
              <>
                Currently configured: <span className="font-mono">{current.masked}</span>{" "}
                <CheckCircle2 size={12} className="inline text-brand-success" />
              </>
            ) : (
              "No key configured for this provider yet."
            )}
          </p>

          {(provider === "openai" || provider === "groq") && (
            <>
              <label htmlFor="model" className="block text-sm font-medium text-slate-700 mb-1">
                Model
              </label>
              <input
                id="model"
                type="text"
                value={modelInput}
                onChange={(e) => setModelInput(e.target.value)}
                placeholder={provider === "openai" ? "gpt-4o-mini" : "llama-3.3-70b-versatile"}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm mb-4 font-mono focus:outline-none focus:ring-2 focus:ring-brand-primary/40"
              />
            </>
          )}

          <label htmlFor="apikey" className="block text-sm font-medium text-slate-700 mb-1">
            API key
          </label>
          <div className="relative mb-2">
            <input
              id="apikey"
              type={showKey ? "text" : "password"}
              value={keyInput}
              onChange={(e) => setKeyInput(e.target.value)}
              placeholder={placeholder}
              autoComplete="off"
              spellCheck={false}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 pr-10 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-brand-primary/40"
            />
            <button
              type="button"
              onClick={() => setShowKey((s) => !s)}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
              title={showKey ? "Hide key" : "Show key"}
            >
              {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
          <p className="text-xs text-slate-500 mb-4">
            Get a key at{" "}
            <a href={keyUrl} target="_blank" rel="noreferrer" className="text-brand-primary hover:underline">
              {keyUrl}
            </a>
            . Your key is stored locally on this machine and used only for your LLM calls.
          </p>

          <button
            onClick={handleSave}
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-lg bg-brand-primary px-4 py-2 text-white font-medium hover:bg-brand-primary-hover disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
          >
            {saving && <Loader2 size={16} className="animate-spin" />}
            {saving ? "Validating & saving…" : "Save & apply"}
          </button>

          {status && (
            <div
              className={`mt-4 flex items-start gap-2 rounded-lg px-3 py-2 text-sm ${
                status.type === "ok"
                  ? "bg-green-50 border border-green-200 text-green-800"
                  : "bg-red-50 border border-red-200 text-red-700"
              }`}
            >
              {status.type === "ok" ? (
                <CheckCircle2 size={16} className="mt-0.5 shrink-0" />
              ) : (
                <AlertCircle size={16} className="mt-0.5 shrink-0" />
              )}
              <span>{status.msg}</span>
            </div>
          )}
        </section>

        <p className="text-xs text-slate-400 mt-4">
          The key applies immediately to new requests — no restart needed. It is also saved to the backend{" "}
          <span className="font-mono">.env</span> so it persists across restarts.
        </p>
      </main>
    </div>
  );
}
