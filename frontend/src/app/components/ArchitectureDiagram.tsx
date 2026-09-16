/**
 * Hand-authored architecture (component) diagram for the Documentation page.
 * Layered structure: Client → FastAPI Backend → Services (LLM / Warehouse /
 * Session store). Styled with the app's brand palette.
 */
export default function ArchitectureDiagram() {
  const DARK = "#0B5563";
  const PRIMARY = "#0F9BAA";
  const SURFACE = "#E6F4F5";
  const LIGHT = "#F4F7F7";
  const TEXT = "#1F2A2D";

  const box = (
    x: number,
    y: number,
    w: number,
    h: number,
    fill: string,
    stroke: string,
  ) => (
    <rect x={x} y={y} width={w} height={h} rx={10} fill={fill} stroke={stroke} strokeWidth={1.5} />
  );

  return (
    <svg
      viewBox="0 0 820 500"
      role="img"
      aria-label="DataMap architecture diagram"
      className="w-full h-auto"
      style={{ maxWidth: 820 }}
    >
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" fill={DARK} />
        </marker>
      </defs>

      {/* Client layer */}
      {box(110, 22, 600, 64, SURFACE, PRIMARY)}
      <text x={410} y={48} textAnchor="middle" fontSize={15} fontWeight={700} fill={DARK}>
        Browser — React SPA
      </text>
      <text x={410} y={70} textAnchor="middle" fontSize={11.5} fill={TEXT}>
        Vite · localhost:5173 · no login (dev auth) · react-markdown UI
      </text>

      {/* Client -> Backend */}
      <line x1={410} y1={86} x2={410} y2={146} stroke={DARK} strokeWidth={1.5} markerEnd="url(#arrow)" />
      <text x={420} y={118} fontSize={11} fill={DARK} fontStyle="italic">HTTPS / JSON</text>

      {/* Backend container */}
      {box(40, 148, 740, 190, LIGHT, DARK)}
      <text x={60} y={172} fontSize={14} fontWeight={700} fill={DARK}>
        FastAPI Backend — localhost:8001
      </text>

      {/* Backend inner components */}
      {box(60, 188, 210, 128, "#FFFFFF", PRIMARY)}
      <text x={165} y={214} textAnchor="middle" fontSize={13} fontWeight={600} fill={DARK}>REST Routers</text>
      <text x={165} y={240} textAnchor="middle" fontSize={11} fill={TEXT}>/sessions · /files</text>
      <text x={165} y={258} textAnchor="middle" fontSize={11} fill={TEXT}>/messages · /mapping</text>
      <text x={165} y={276} textAnchor="middle" fontSize={11} fill={TEXT}>/dart · /data · /extract</text>

      {box(300, 188, 210, 128, "#FFFFFF", PRIMARY)}
      <text x={405} y={214} textAnchor="middle" fontSize={13} fontWeight={600} fill={DARK}>Identity &amp; Auth</text>
      <text x={405} y={244} textAnchor="middle" fontSize={11} fill={TEXT}>dev (default)</text>
      <text x={405} y={262} textAnchor="middle" fontSize={11} fill={TEXT}>launchpad_sso · iap</text>

      {box(540, 188, 210, 128, "#FFFFFF", PRIMARY)}
      <text x={645} y={214} textAnchor="middle" fontSize={13} fontWeight={600} fill={DARK}>AI Agents</text>
      <text x={645} y={240} textAnchor="middle" fontSize={11} fill={TEXT}>Google ADK</text>
      <text x={645} y={258} textAnchor="middle" fontSize={11} fill={TEXT}>profiling · mapping</text>
      <text x={645} y={276} textAnchor="middle" fontSize={11} fill={TEXT}>anomaly · data dict</text>

      {/* Backend -> Services arrows */}
      <line x1={155} y1={338} x2={155} y2={398} stroke={DARK} strokeWidth={1.5} markerEnd="url(#arrow)" />
      <text x={120} y={372} fontSize={10.5} fill={DARK} fontStyle="italic">generate</text>
      <line x1={410} y1={338} x2={410} y2={398} stroke={DARK} strokeWidth={1.5} markerEnd="url(#arrow)" />
      <text x={388} y={372} fontSize={10.5} fill={DARK} fontStyle="italic">SQL</text>
      <line x1={665} y1={338} x2={665} y2={398} stroke={DARK} strokeWidth={1.5} markerEnd="url(#arrow)" />
      <text x={628} y={372} fontSize={10.5} fill={DARK} fontStyle="italic">persist</text>

      {/* Services layer */}
      {box(40, 400, 230, 76, SURFACE, PRIMARY)}
      <text x={155} y={428} textAnchor="middle" fontSize={13} fontWeight={600} fill={DARK}>LLM Provider</text>
      <text x={155} y={450} textAnchor="middle" fontSize={11} fill={TEXT}>Gemini / Groq (LiteLLM)</text>

      {box(295, 400, 230, 76, SURFACE, PRIMARY)}
      <text x={410} y={428} textAnchor="middle" fontSize={13} fontWeight={600} fill={DARK}>Local Warehouse</text>
      <text x={410} y={450} textAnchor="middle" fontSize={11} fill={TEXT}>SQLite + sqlite-vec</text>

      {box(550, 400, 230, 76, SURFACE, PRIMARY)}
      <text x={665} y={428} textAnchor="middle" fontSize={13} fontWeight={600} fill={DARK}>Session Store</text>
      <text x={665} y={450} textAnchor="middle" fontSize={11} fill={TEXT}>ADK DatabaseSessionService</text>
    </svg>
  );
}
