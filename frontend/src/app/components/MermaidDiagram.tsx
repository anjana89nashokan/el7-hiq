import { useEffect, useRef, useState } from "react";
import mermaid from "mermaid";

let initialized = false;

function ensureInit() {
  if (initialized) return;
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: "base",
    fontFamily: "inherit",
    themeVariables: {
      primaryColor: "#E6F4F5",
      primaryBorderColor: "#0F9BAA",
      primaryTextColor: "#1F2A2D",
      lineColor: "#0B5563",
      actorBkg: "#E6F4F5",
      actorBorder: "#0F9BAA",
      actorTextColor: "#0B5563",
      signalColor: "#0B5563",
      signalTextColor: "#1F2A2D",
      labelBoxBkgColor: "#F4F7F7",
      labelBoxBorderColor: "#0F9BAA",
      noteBkgColor: "#FEF9E7",
      noteBorderColor: "#D97706",
    },
  });
  initialized = true;
}

/** Renders a Mermaid diagram definition to inline SVG. */
export default function MermaidDiagram({
  chart,
  className = "",
}: {
  readonly chart: string;
  readonly className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const idRef = useRef("mmd-" + Math.random().toString(36).slice(2));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    ensureInit();
    let cancelled = false;
    mermaid
      .render(idRef.current, chart)
      .then(({ svg }) => {
        if (!cancelled && ref.current) ref.current.innerHTML = svg;
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [chart]);

  if (error) {
    return (
      <pre className="text-xs text-red-600 whitespace-pre-wrap bg-red-50 border border-red-200 rounded p-3">
        {error}
      </pre>
    );
  }

  return <div ref={ref} className={`w-full overflow-x-auto flex justify-center ${className}`} />;
}
