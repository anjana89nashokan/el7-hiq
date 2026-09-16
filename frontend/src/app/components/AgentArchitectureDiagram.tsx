/**
 * Low-level agentic-AI architecture (component diagram): the ADK Runner drives
 * an Orchestrator agent that delegates to specialized sub-agents, each owning
 * FunctionTools that hit the warehouse and the LLM. Brand-styled SVG.
 */
export default function AgentArchitectureDiagram() {
  const DARK = "#0B5563";
  const PRIMARY = "#0F9BAA";
  const SURFACE = "#E6F4F5";
  const LIGHT = "#F4F7F7";
  const TEXT = "#1F2A2D";

  const rect = (x: number, y: number, w: number, h: number, fill: string, stroke: string) => (
    <rect x={x} y={y} width={w} height={h} rx={9} fill={fill} stroke={stroke} strokeWidth={1.4} />
  );

  type Line = { t: string; b?: boolean };
  const agent = (x: number, y: number, title: string, lines: Line[]) => (
    <g key={`${x}-${y}`}>
      {rect(x, y, 276, 104, "#FFFFFF", PRIMARY)}
      <text x={x + 12} y={y + 22} fontSize={12.5} fontWeight={700} fill={DARK}>{title}</text>
      {lines.map((ln, i) => (
        <text
          key={ln.t}
          x={x + 12}
          y={y + 42 + i * 17}
          fontSize={10.5}
          fill={ln.b ? DARK : TEXT}
          fontWeight={ln.b ? 600 : 400}
        >
          {ln.t}
        </text>
      ))}
    </g>
  );

  const svc = (x: number, title: string, sub: string) => (
    <g key={title}>
      {rect(x, 432, 276, 60, SURFACE, PRIMARY)}
      <text x={x + 138} y={458} textAnchor="middle" fontSize={12.5} fontWeight={600} fill={DARK}>{title}</text>
      <text x={x + 138} y={478} textAnchor="middle" fontSize={10.5} fill={TEXT}>{sub}</text>
    </g>
  );

  return (
    <svg viewBox="0 0 900 580" role="img" aria-label="Agentic AI architecture" className="w-full h-auto" style={{ maxWidth: 900 }}>
      <defs>
        <marker id="aarrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" fill={DARK} />
        </marker>
      </defs>

      {/* Runner */}
      {rect(330, 12, 240, 46, SURFACE, DARK)}
      <text x={450} y={32} textAnchor="middle" fontSize={12.5} fontWeight={700} fill={DARK}>ADK Runner — runner.run_async()</text>
      <text x={450} y={49} textAnchor="middle" fontSize={10} fill={TEXT}>driven by /messages · /dart endpoints</text>
      <line x1={450} y1={58} x2={450} y2={82} stroke={DARK} strokeWidth={1.4} markerEnd="url(#aarrow)" />

      {/* Orchestrator */}
      {rect(300, 84, 300, 52, LIGHT, DARK)}
      <text x={450} y={106} textAnchor="middle" fontSize={13} fontWeight={700} fill={DARK}>Orchestrator Agent (root_agent · LlmAgent)</text>
      <text x={450} y={124} textAnchor="middle" fontSize={10.5} fill={TEXT}>routes intent / [stage] → transfer_to_agent</text>
      <line x1={450} y1={136} x2={450} y2={166} stroke={DARK} strokeWidth={1.4} markerEnd="url(#aarrow)" />
      <text x={462} y={156} fontSize={10} fontStyle="italic" fill={DARK}>delegates (sub_agents)</text>

      {/* Sub-agents grid (2 x 3) */}
      {agent(12, 170, "profiling_agent", [
        { t: "Agent · 2 tools" },
        { t: "intelligent_profiling_tool" },
        { t: "relationship_analysis_tool" },
        { t: "→ OutputFormatProfiling", b: true },
      ])}
      {agent(309, 170, "profiling_agent_anomaly", [
        { t: "Agent · 1 tool" },
        { t: "data_anomaly_analysis_tool" },
        { t: "" },
        { t: "→ OutputFormatAnomaly", b: true },
      ])}
      {agent(606, 170, "data_dict_agent", [
        { t: "LoopAgent" },
        { t: "generator → saver" },
        { t: "append_chunk_to_bq" },
        { t: "→ DataDictionaryResponse", b: true },
      ])}
      {agent(12, 292, "smart_similarity_agent", [
        { t: "SequentialAgent" },
        { t: "semantic_matching →" },
        { t: "overlap_validation" },
        { t: "→ SemanticMatchingResponse", b: true },
      ])}
      {agent(309, 292, "dart_suggestion_agent", [
        { t: "LlmAgent · 1 tool" },
        { t: "dart_suggestions_tool" },
        { t: "(vector search + MDR)" },
        { t: "→ DartSuggestionResponse", b: true },
      ])}
      {agent(606, 292, "metadata_fill_agent", [
        { t: "LlmAgent · PlanReAct" },
        { t: "bigquery_toolset" },
        { t: "execution tool" },
        { t: "→ MetadataFillResponse", b: true },
      ])}

      {/* Arrows to shared services */}
      <line x1={150} y1={396} x2={150} y2={430} stroke={DARK} strokeWidth={1.4} markerEnd="url(#aarrow)" />
      <text x={158} y={418} fontSize={9.5} fontStyle="italic" fill={DARK}>reason</text>
      <line x1={447} y1={396} x2={447} y2={430} stroke={DARK} strokeWidth={1.4} markerEnd="url(#aarrow)" />
      <text x={455} y={418} fontSize={9.5} fontStyle="italic" fill={DARK}>SQL</text>
      <line x1={744} y1={396} x2={744} y2={430} stroke={DARK} strokeWidth={1.4} markerEnd="url(#aarrow)" />
      <text x={752} y={418} fontSize={9.5} fontStyle="italic" fill={DARK}>persist</text>

      {/* Shared services */}
      {svc(12, "LLM Provider", "Gemini / Groq · function-calling")}
      {svc(309, "Local Warehouse", "SQLite · stats / overlap / keys")}
      {svc(606, "Session State", "output_key → state_delta")}

      {/* Finalization note */}
      {rect(12, 512, 870, 52, "#FEF9E7", "#D97706")}
      <text x={447} y={534} textAnchor="middle" fontSize={11} fontWeight={600} fill="#92400E">
        Every agent finalizes via set_model_response(text_response, tool_response)
      </text>
      <text x={447} y={552} textAnchor="middle" fontSize={10.5} fill="#92400E">
        → validated against the agent&apos;s output_schema → written to session state_delta → returned by the endpoint
      </text>
    </svg>
  );
}
