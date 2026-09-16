/**
 * Low-level backend internals: the real module layers (API → Agent Runtime →
 * Domain logic → Persistence) with actual file/module names and the SQLite
 * stores. Brand-styled SVG component diagram (not a flowchart).
 */
export default function BackendArchitectureDiagram() {
  const DARK = "#0B5563";
  const PRIMARY = "#0F9BAA";
  const SURFACE = "#E6F4F5";
  const LIGHT = "#F4F7F7";
  const TEXT = "#1F2A2D";

  const rect = (x: number, y: number, w: number, h: number, fill: string, stroke: string, dash = "") => (
    <rect x={x} y={y} width={w} height={h} rx={8} fill={fill} stroke={stroke} strokeWidth={1.3} strokeDasharray={dash} />
  );

  // inner module box: bold title + small sub-lines
  const mod = (x: number, y: number, w: number, h: number, title: string, lines: string[]) => (
    <g key={`${x}-${y}-${title}`}>
      {rect(x, y, w, h, "#FFFFFF", PRIMARY)}
      <text x={x + 10} y={y + 20} fontSize={11.5} fontWeight={700} fill={DARK}>{title}</text>
      {lines.map((ln, i) => (
        <text key={ln} x={x + 10} y={y + 37 + i * 15} fontSize={9.8} fill={TEXT}>{ln}</text>
      ))}
    </g>
  );

  const bandTitle = (x: number, y: number, t: string) => (
    <text x={x} y={y} fontSize={12} fontWeight={700} fill={DARK} letterSpacing={0.3}>{t}</text>
  );

  const vArrow = (x: number, y1: number, y2: number) => (
    <line x1={x} y1={y1} x2={x} y2={y2} stroke={DARK} strokeWidth={1.3} markerEnd="url(#bk-arrow)" />
  );

  return (
    <svg viewBox="0 0 920 690" role="img" aria-label="Backend internal architecture" className="w-full h-auto" style={{ maxWidth: 920 }}>
      <defs>
        <marker id="bk-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" fill={DARK} />
        </marker>
      </defs>

      {/* Client */}
      {rect(250, 12, 420, 40, SURFACE, DARK)}
      <text x={460} y={37} textAnchor="middle" fontSize={12.5} fontWeight={700} fill={DARK}>Browser — React SPA (Vite :5173)</text>
      {vArrow(460, 52, 72)}

      {/* API layer */}
      {rect(20, 74, 880, 116, LIGHT, DARK)}
      {bandTitle(32, 92, "API Layer — FastAPI · api/main.py (CORS + router registration)")}
      {mod(32, 100, 280, 80, "Routers — api/routers/", ["sessions · files · messages", "messages_stream_new · dart_suggestion", "data · mapping · quality"])}
      {mod(324, 100, 268, 80, "Auth — api/dependencies/auth.py", ["resolve_current_user()", "dev · launchpad_sso · iap"])}
      {mod(604, 100, 284, 80, "Schemas — api/models.py", ["Pydantic request /", "response models"])}
      {vArrow(460, 190, 208)}

      {/* Agent runtime */}
      {rect(20, 210, 880, 126, LIGHT, DARK)}
      {bandTitle(32, 228, "Agent Runtime — Google ADK")}
      {mod(32, 236, 206, 90, "ADK Runner", ["runner.run_async()", "RunConfig — context", "window compression"])}
      {mod(248, 236, 206, 90, "Orchestrator + sub-agents", ["agents/…/agent.py", "root_agent → 6 agents", "transfer_to_agent"])}
      {mod(464, 236, 206, 90, "FunctionTools", ["wrap tool fns", "set_model_response", "+ output_schema"])}
      {mod(680, 236, 208, 90, "settings.py patches", ["genai client · model", "output parse", "compaction rehydrate"])}
      {vArrow(460, 336, 354)}

      {/* Domain logic */}
      {rect(20, 356, 880, 116, LIGHT, DARK)}
      {bandTitle(32, 374, "Domain Logic — utils/ (tool implementations)")}
      {mod(32, 382, 206, 80, "profiling_dispatcher", ["→ profiling_functions", "(_batched)"])}
      {mod(248, 382, 206, 80, "relationship_functions", ["PK/FK · overlap", "composite (AK) keys"])}
      {mod(464, 382, 206, 80, "data_anomaly_functions", ["(_batched)", "format · outlier · dup"])}
      {mod(680, 382, 208, 80, "llm_helper · rate_limiter", ["profiling_artifact_store", "bg_query_utils"])}
      {vArrow(460, 472, 490)}

      {/* Persistence & integration */}
      {bandTitle(20, 506, "Persistence & Integration")}
      {mod(20, 514, 282, 70, "Local Warehouse", ["local_warehouse.py", "data/warehouse.db — uploaded tables"])}
      {mod(319, 514, 282, 70, "Vector Store", ["sqlite-vec", "data/vectors.db — similarity / DART"])}
      {mod(618, 514, 282, 70, "ADK Sessions", ["DatabaseSessionService", "data/adk_sessions.db — agent state"])}
      {mod(20, 600, 282, 70, "App DB", ["db/engine.py + repositories.py", "data/app.db — session metadata"])}
      {mod(319, 600, 282, 70, "Artifacts & Reports", ["data/artifacts/ · reports/*.html", "profiling_artifact_store.py"])}
      {/* LLM provider — dashed to signal external */}
      {rect(618, 600, 282, 70, SURFACE, PRIMARY, "5 3")}
      <text x={628} y={620} fontSize={11.5} fontWeight={700} fill={DARK}>LLM Provider (external)</text>
      <text x={628} y={637} fontSize={9.8} fill={TEXT}>genai → Gemini Dev API</text>
      <text x={628} y={652} fontSize={9.8} fill={TEXT}>litellm → Groq</text>
    </svg>
  );
}
