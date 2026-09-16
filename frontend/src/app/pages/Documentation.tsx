import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  BookOpen,
  Bot,
  CheckCircle2,
  Download,
  FileText,
  KeyRound,
  Layers,
  Network,
  Settings,
  Workflow,
} from "lucide-react";
import MermaidDiagram from "../components/MermaidDiagram";
import ArchitectureDiagram from "../components/ArchitectureDiagram";
import BackendArchitectureDiagram from "../components/BackendArchitectureDiagram";
import AgentArchitectureDiagram from "../components/AgentArchitectureDiagram";
import { API_BASE_URL } from "../config/env";

/**
 * In-app Documentation (Sidebar → under Chat). A friendly, card-based user
 * guide (numbered steps, prerequisite chips, tips) plus the architecture,
 * agentic, and low-level reference — adapted to the standalone, no-login app.
 */

type IconType = LucideIcon;

const mdComponents = {
  table: ({ children }: { children?: ReactNode }) => (
    <div className="my-4 w-full overflow-x-auto border border-gray-200 rounded-lg">
      <table className="w-full divide-y divide-gray-200 border-collapse text-sm">{children}</table>
    </div>
  ),
  th: ({ children }: { children?: ReactNode }) => (
    <th className="bg-brand-surface px-3 py-2 text-left font-semibold text-brand-darkblue">{children}</th>
  ),
  td: ({ children }: { children?: ReactNode }) => (
    <td className="px-3 py-2 border-t border-gray-100 align-top">{children}</td>
  ),
  code: ({ className, children, ...props }: { className?: string; children?: ReactNode }) => {
    const isBlock = /language-/.test(className ?? "");
    if (isBlock) return <code className={className} {...props}>{children}</code>;
    return <code className="rounded bg-brand-surface px-1.5 py-0.5 text-[0.85em]" {...props}>{children}</code>;
  },
};

function Md({ children }: { readonly children: string }) {
  return (
    <div className="prose prose-slate max-w-none prose-sm prose-headings:text-brand-darkblue prose-a:text-brand-primary prose-code:before:content-none prose-code:after:content-none prose-pre:bg-brand-charcoal">
      <Markdown remarkPlugins={[remarkGfm]} components={mdComponents}>{children}</Markdown>
    </div>
  );
}

function Card({ id, children }: { readonly id?: string; readonly children: ReactNode }) {
  return (
    <section id={id} className="scroll-mt-6 bg-white border border-gray-200 rounded-xl p-6 shadow-sm mb-6">
      {children}
    </section>
  );
}

function SectionHeader({ icon: Icon, children }: { readonly icon: IconType; readonly children: ReactNode }) {
  return (
    <h2 className="flex items-center gap-2 text-lg font-bold text-brand-darkblue mb-4">
      <Icon size={18} className="text-brand-primary" />
      {children}
    </h2>
  );
}

function Chip({ icon: Icon, children }: { readonly icon: IconType; readonly children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-gray-200 bg-white px-3 py-1.5 text-xs text-slate-600">
      <Icon size={13} className="text-brand-primary" />
      {children}
    </span>
  );
}

function Step({ n, title, children }: { readonly n: number; readonly title: string; readonly children: ReactNode }) {
  return (
    <div className="flex gap-4 py-4 border-t border-gray-100 first:border-t-0 first:pt-0">
      <div className="shrink-0 w-7 h-7 rounded-full bg-brand-primary text-white text-sm font-semibold flex items-center justify-center mt-0.5">
        {n}
      </div>
      <div className="min-w-0 flex-1">
        <h4 className="font-semibold text-slate-800 mb-1">{title}</h4>
        <div className="text-sm text-slate-600 space-y-2">{children}</div>
      </div>
    </div>
  );
}

function Tip({ children }: { readonly children: ReactNode }) {
  return (
    <div className="mt-2 rounded-md bg-brand-surface/70 border border-brand-primary/30 px-3 py-2 text-sm text-slate-700">
      <span className="font-semibold text-brand-darkblue">Tip:</span> {children}
    </div>
  );
}

function Lead({ label, children }: { readonly label: string; readonly children: ReactNode }) {
  return (
    <p>
      <strong className="text-slate-800">{label}</strong> {children}
    </p>
  );
}

const Code = ({ children }: { readonly children: ReactNode }) => (
  <code className="rounded bg-brand-surface px-1.5 py-0.5 text-[0.85em]">{children}</code>
);

const SEQUENCE = `sequenceDiagram
    actor U as User
    participant FE as React SPA
    participant API as FastAPI Backend
    participant WH as Local Warehouse
    participant AG as AI Agent (ADK)
    participant LLM as Gemini / Groq

    U->>FE: Open app (no login, dev mode)
    U->>FE: Create session, upload CSV/Excel
    FE->>API: POST /sessions/app, /files/upload-batch
    API->>WH: Stage raw files
    FE->>API: POST /files/process-files
    API->>WH: Load tables, build profiling report
    API-->>FE: Tables, data-quality scores, report URL
    U->>FE: Run a pipeline step (e.g. profiling)
    FE->>API: POST /messages/send
    API->>AG: Invoke agent (prompt + tools)
    AG->>WH: Query table metadata / stats
    AG->>LLM: Generate structured response
    LLM-->>AG: text_response + tool_response
    AG-->>API: Final set_model_response
    API-->>FE: { text_response, tool_response }
    FE-->>U: Render results (markdown + structured)`;

const AGENT_LOOP = `sequenceDiagram
    participant API as Endpoint
    participant R as ADK Runner
    participant O as Orchestrator
    participant A as Sub-agent
    participant LLM as LLM (Gemini/Groq)
    participant T as FunctionTool
    participant WH as Warehouse

    API->>R: run_async(session, message, run_config)
    R->>O: user message + session state
    O->>LLM: instruction + tool schemas + history
    LLM-->>O: transfer_to_agent(sub-agent)
    O->>A: hand off by intent / [stage]
    A->>LLM: agent prompt + its tool schemas
    LLM-->>A: function_call: tool(args)
    A->>T: execute(args, tool_context)
    T->>WH: SQL — stats, overlap, keys
    WH-->>T: rows / metrics
    T-->>A: function_response (structured dict)
    A->>LLM: append function_response to context
    Note over A,LLM: loop until the model is ready to answer
    LLM-->>A: function_call: set_model_response(text_response, tool_response)
    A->>A: validate vs output_schema → state_delta[output_key]
    A-->>R: final event
    R-->>API: event stream
    API-->>API: shape { text_response, tool_response, status }`;

export default function Documentation() {
  const sampleHref = `${API_BASE_URL}/files/sample-data`;

  return (
    <div className="min-h-screen bg-brand-light text-slate-900 font-sans pb-12">
      <main className="max-w-5xl mx-auto p-6 lg:p-8">
        {/* Before you start */}
        <Card>
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Before you start</h3>
          <div className="flex flex-wrap gap-2">
            <Chip icon={CheckCircle2}>Node.js 18+ and Python 3.12 installed</Chip>
            <Chip icon={KeyRound}>A valid Gemini key in <span className="font-mono ml-1">datamap_backend/.env</span></Chip>
            <Chip icon={FileText}>Source CSV files (or the sample set below)</Chip>
          </div>
        </Card>

        {/* 1. User Guide */}
        <Card id="user-guide">
          <SectionHeader icon={BookOpen}>1. User Guide — how to use DataMap</SectionHeader>

          <Step n={1} title="Start DataMap (no login)">
            <p>
              From the repo root run <Code>./start.sh</Code>, then open{" "}
              <Code>http://localhost:5173</Code>. Standalone <strong>dev mode</strong> has no sign-in —
              the app opens straight to your workspace.
            </p>
            <Lead label="You get:">a local DataMap workspace scoped to a default local user.</Lead>
            <Tip>If you ever see a "Network Error", check the backend is up and the frontend is on an allowed origin (5173).</Tip>
          </Step>

          <Step n={2} title="Add your Gemini key (one-time, BYOK)">
            <p>
              DataMap's AI steps use <strong>your own</strong> model key. Open{" "}
              <Code>datamap_backend/.env</Code> and set <Code>GOOGLE_API_KEY=...</Code> (or switch to
              Groq with <Code>LLM_PROVIDER=groq</Code> + <Code>GROQ_API_KEY</Code>), then restart.
            </p>
            <Lead label="You provide:">a valid Gemini Developer API key (from aistudio.google.com/apikey).</Lead>
            <Lead label="You get:">AI steps that read your key at request time — nothing hard-coded.</Lead>
            <Tip>
              Verify a key before saving:{" "}
              <Code>curl "https://generativelanguage.googleapis.com/v1beta/models?key=YOUR_KEY"</Code> should list models.
            </Tip>
          </Step>

          <Step n={3} title="Create a session & upload source data">
            <p>
              Click <strong>New Session</strong>, then <strong>New Profiling / Upload</strong> and add
              your source files (CSV). Every piece of work lives in a resumable session under{" "}
              <strong>Recent Sessions</strong>.
            </p>
            <p>
              Drop your CSVs into the main <strong>Source data</strong> area (you can select several
              at once). The <strong>Data Dictionary</strong> and <strong>BRD Document</strong> areas
              are optional — leave them empty if you don't have those files.
            </p>
            <p className="text-slate-500">
              Using the sample set? Put all four CSVs (<span className="font-mono text-xs">groups, providers, members, medical_claims</span>)
              into <strong>Source data</strong>, and skip Data Dictionary / BRD.
            </p>
            <Lead label="You get:">your files loaded into the local warehouse plus an initial profiling report.</Lead>
            <a
              href={sampleHref}
              className="inline-flex items-center gap-2 mt-1 rounded-lg bg-brand-primary px-3.5 py-2 text-sm text-white font-medium hover:bg-brand-primary-hover transition-colors no-underline"
            >
              <Download size={16} strokeWidth={1.8} />
              Download sample data (.zip)
            </a>
            <Tip>No data handy? Grab the sample set above (providers, members, medical_claims, groups).</Tip>
          </Step>

          <Step n={4} title="Run the profiling pipeline">
            <p>
              Walk the 8-step pipeline (Dataset Overview → Relationship Analysis → … → Detailed
              Profiling). Each step is AI-driven and renders as readable text plus structured tables.
            </p>
            <Lead label="You get:">profiling, relationships, a data dictionary, anomalies, and a metadata template.</Lead>
            <Tip>A step occasionally shows "Something went wrong, Try again" — that's a transient model hiccup; click <strong>Retry</strong>.</Tip>
          </Step>

          <h4 className="text-base font-semibold text-brand-darkblue mt-6 mb-2">What to upload for each workflow</h4>
          <p className="text-sm text-slate-600 mb-3">
            Different workflows expect different inputs. Upload the files into the matching field —
            required fields are marked, optional ones can be skipped.
          </p>
          <Md>{`**Profiling** — *New Profiling → Upload*

| Field | Required? | Accepts | What to upload |
|-------|-----------|---------|----------------|
| **Source data** (main drag-and-drop) | Required | CSV | The data tables to profile (e.g. \`providers.csv\`, \`members.csv\`). Upload all of them together. |
| **Data Dictionary** | Optional | CSV, XLSX, TXT | A column dictionary, if you have one |
| **BRD Document** | Optional | PDF, DOCX | A business-requirements doc, if you have one |

**Extract** — *New Session → Extract*

| Field | Required? | Accepts | What to upload |
|-------|-----------|---------|----------------|
| **BRD Document** | Required | PDF, DOCX | The Business Requirements Document |
| **File Layout Document** | Required | PDF, DOCX, XLSX | The source file layout / spec |
| **Transcript** | Optional | PDF, DOCX | A meeting / call transcript, if you have one |

> For the bundled **sample data** (Profiling): drop all four CSVs (\`groups\`, \`providers\`,
> \`members\`, \`medical_claims\`) into **Source data**, and leave Data Dictionary / BRD empty.`}</Md>
        </Card>

        {/* 2. Architecture */}
        <Card id="architecture">
          <SectionHeader icon={Network}>2. Architecture</SectionHeader>
          <Md>{`Two tiers: a **React single-page app** talks to a **FastAPI backend** that hosts
the REST API, the AI agents (Google ADK), and a local SQLite warehouse. The
backend calls an LLM provider (Gemini by default, or Groq). Everything runs
locally — nothing leaves your machine except the LLM calls.`}</Md>
          <div className="my-5 border border-gray-200 rounded-lg p-4 bg-brand-light/40">
            <ArchitectureDiagram />
          </div>
        </Card>

        {/* 3. Low-Level Architecture */}
        <Card id="low-level">
          <SectionHeader icon={Layers}>3. Low-Level Architecture</SectionHeader>
          <Md>{`Inside the backend a request travels through four module layers — **API → Agent
Runtime → Domain Logic → Persistence**. The diagram names the actual modules and
the SQLite stores.`}</Md>
          <div className="my-5 border border-gray-200 rounded-lg p-4 bg-brand-light/40">
            <BackendArchitectureDiagram />
          </div>

          <h4 className="text-base font-semibold text-brand-darkblue mt-5 mb-2">Request lifecycle — a profiling step, end to end</h4>
          <Md>{`1. **Frontend** — \`pages/ProfilingResult\` → \`end-points/profilingResultApi.ts\` POSTs \`/messages/send\` via \`utils/axios-interceptor.ts\`.
2. **Routing & auth** — \`api/main.py\` → \`api/routers/messages.py:send_message()\`; \`auth.py:resolve_current_user()\` resolves the user (dev → \`local-user\`).
3. **Session & runner** — parse \`[stage]\`, load session via \`adk_runtime.py:get_session_service()\` (\`DatabaseSessionService\`, \`adk_sessions.db\`), build the App + \`Runner\`, inject state, token-guard the message.
4. **Orchestration** — \`runner.run_async()\` streams events; \`root_agent\` delegates to \`profiling_agent\` (\`transfer_to_agent\`).
5. **Tool execution** — the LLM (Gemini/Groq via the \`canonical_model\` patch) emits a \`function_call\`; ADK runs \`intelligent_profiling_tool\` (\`profiling_dispatcher.py\` → \`profiling_functions.py\`) which queries \`local_warehouse.py\` (\`warehouse.db\`).
6. **Finalize** — the LLM calls \`set_model_response\`; ADK validates against \`OutputFormatProfiling\` and writes \`state_delta["final_profiling_response"]\`.
7. **Respond** — the handler shapes \`{ text_response, tool_response, status }\`, persists via \`profiling_artifact_store.py\`, returns JSON.
8. **Render** — the SPA renders \`text_response\` (markdown) + the structured \`tool_response\`.`}</Md>

          <h4 className="text-base font-semibold text-brand-darkblue mt-5 mb-2">Storage layer (all local SQLite + files)</h4>
          <Md>{`| Store | Path | Holds | Module |
|---|---|---|---|
| Analytical warehouse | \`data/warehouse.db\` | Uploaded tables (\`sttm_*\`), queried with SQL | \`utils/local_warehouse.py\` |
| Vector store | \`data/vectors.db\` | Embeddings for similarity / DART | \`sqlite-vec\` |
| ADK sessions | \`data/adk_sessions.db\` | Agent history + \`state_delta\` | \`DatabaseSessionService\` |
| App DB | \`data/app.db\` | Session metadata (titles, run ids) | \`db/engine.py\`, \`db/repositories.py\` |
| Artifacts | \`data/artifacts/\` | Raw files + persisted step responses | \`utils/profiling_artifact_store.py\` |
| Reports | \`reports/\` | ydata-profiling HTML (served at \`/reports\`) | \`api/routers/files.py\` |`}</Md>

          <h4 className="text-base font-semibold text-brand-darkblue mt-5 mb-2">Frontend internals</h4>
          <Md>{`| Area | Path | Role |
|---|---|---|
| Entry + routing | \`src/main.tsx\`, \`src/App.tsx\` | Bootstrap, \`createBrowserRouter\` |
| App shell | \`src/components/\` (Layout, Header, Sidebar, ChatSidebar) | Navigation + chat |
| Screens | \`src/pages/\` | Route screens |
| API wrappers | \`src/end-points/\` | One Axios module per domain |
| Transport | \`src/utils/axios-interceptor.ts\` | Base URL + identity headers |
| State | \`src/state/\` (Redux), \`src/contexts/ChatContext\` | App + chat state |
| Streaming | \`src/hooks/useSSEStream.ts\` | SSE for the streaming pipeline |
| Config | \`src/config/\` | Env + branding constants |`}</Md>
        </Card>

        {/* 4. Agentic AI Architecture */}
        <Card id="agentic">
          <SectionHeader icon={Bot}>4. Agentic AI Architecture</SectionHeader>
          <Md>{`The AI runs on **Google ADK**. Each step spins up an ADK \`Runner\` that drives an
**Orchestrator agent** (\`root_agent\`). It reads the user's intent and the bracketed
\`[stage]\` and **delegates** to a specialized sub-agent (\`transfer_to_agent\`). Each
sub-agent owns a few **FunctionTools** and a strict **\`output_schema\`**.`}</Md>
          <div className="my-5 border border-gray-200 rounded-lg p-4 bg-brand-light/40">
            <AgentArchitectureDiagram />
          </div>

          <h4 className="text-base font-semibold text-brand-darkblue mt-5 mb-2">How an agent works internally</h4>
          <div className="my-5 border border-gray-200 rounded-lg p-4 bg-white">
            <MermaidDiagram chart={AGENT_LOOP} />
          </div>

          <h4 className="text-base font-semibold text-brand-darkblue mt-5 mb-2">Agents</h4>
          <Md>{`| Agent | ADK type | Tools | Output schema |
|---|---|---|---|
| \`root_agent\` | LlmAgent | \`ground_truth_tool\` + routing | — (router) |
| \`profiling_agent\` | Agent | \`intelligent_profiling_tool\`, \`relationship_analysis_tool\` | \`OutputFormatProfiling\` |
| \`profiling_agent_anomaly\` | Agent | \`data_anomaly_analysis_tool\` | \`OutputFormatAnomaly\` |
| \`data_dict_agent\` | LoopAgent | \`append_chunk_to_bq\`, \`signal_exit\` | \`DataDictionaryResponse\` |
| \`smart_similarity_agent\` | SequentialAgent | \`fetch_metadata_tool\` (2 phases) | \`SemanticMatchingResponse\` |
| \`dart_suggestion_agent\` | LlmAgent | \`dart_suggestions_tool\` | \`DartSuggestionResponse\` |
| \`metadata_fill_agent\` | LlmAgent (PlanReAct) | \`bigquery_toolset\`, execution tool | \`MetadataFillHITLResponse\` |`}</Md>

          <h4 className="text-base font-semibold text-brand-darkblue mt-5 mb-2">Tool calls — inputs, work, and outputs</h4>
          <Md>{`| Tool | Receives | Does | Returns | DB | LLM |
|---|---|---|---|---|---|
| \`intelligent_profiling_tool\` | \`table_references[]\` | Stats, samples, PK/composite-key picks | \`result[]\` (quality, column analysis) | Yes | Yes |
| \`relationship_analysis_tool\` | \`table_references\` | PK/FK + cross-table overlap, AK keys | \`relationship_analysis_tool_response\` | Yes | Yes |
| \`data_anomaly_analysis_tool\` | \`table_references\`, sensitivity | Format/outlier/dup detection | \`table_anomaly_reports\` | Yes | No |
| \`fetch_metadata_tool\` | source + reference tables | Metadata for semantic + overlap match | match candidates + scores | Yes | Yes |
| \`dart_suggestions_tool\` | source columns | Vector search + MDR filter | suggested reference tables | Yes | Yes |
| \`set_model_response\` | \`text_response\`, \`tool_response\` | Finalize against \`output_schema\` | validated answer | No | — |`}</Md>
        </Card>

        {/* 5. The Profiling Pipeline */}
        <Card id="pipeline">
          <SectionHeader icon={Workflow}>5. The Profiling Pipeline</SectionHeader>
          <Md>{`After upload + processing, the UI walks 8 steps. Steps 1–7 are AI-driven; the
warehouse load and HTML report come from processing.

| # | Step | What it does | Endpoint |
|---|---|---|---|
| 1 | Dataset Overview | Profiling report, counts, quality score, summary | \`POST /messages/send\` |
| 2 | Relationship Analysis | PK/FK + cross-table relationships | \`POST /messages/send\` |
| 3 | Data Dictionary | Column-level dictionary, saved as a table | \`POST /messages/data-dictionary\` |
| 4 | Similarity Check | Compares columns vs reference tables | \`POST /messages/similarity-check\` |
| 5 | Reference Suggestion | Reference-table suggestions (vector search) | \`POST /dart/dart-suggestion\` |
| 6 | Data Anomaly Analysis | Anomalies with severity scoring | \`POST /messages/send\` |
| 7 | Metadata Template | Fills metadata + file-specs template | \`POST /messages/metadata_fill\` |
| 8 | Detailed Profiling | Full ydata-profiling HTML report | \`GET /reports/{id}.html\` |`}</Md>
          <h4 className="text-base font-semibold text-brand-darkblue mt-5 mb-2">Request flow</h4>
          <div className="my-4 border border-gray-200 rounded-lg p-4 bg-white">
            <MermaidDiagram chart={SEQUENCE} />
          </div>
        </Card>

        {/* 6. Setup, Configuration & Troubleshooting */}
        <Card id="setup">
          <SectionHeader icon={Settings}>6. Setup, Configuration & Troubleshooting</SectionHeader>
          <Md>{`**Install from scratch**

1. Download or clone the repo, then \`cd\` into it.
2. \`cp datamap_backend/.env.example datamap_backend/.env\`
3. Add your key to \`.env\` (\`GOOGLE_API_KEY=...\`, or \`LLM_PROVIDER=groq\` + \`GROQ_API_KEY\`).
4. \`./start.sh\` (installs deps on first run), then open <http://localhost:5173>.

Override ports with \`BACKEND_PORT\` / \`FRONTEND_PORT\`. Stop with **Ctrl+C**.

**Configuration** (\`datamap_backend/.env\`)

| Variable | Default | Purpose |
|---|---|---|
| \`LLM_PROVIDER\` | auto (\`gemini\`) | \`gemini\` or \`groq\` |
| \`GOOGLE_API_KEY\` | — | Gemini Developer API key |
| \`GROQ_API_KEY\` | — | Groq key (with \`LLM_PROVIDER=groq\`) |
| \`APP_SESSION_AUTH_MODE\` | \`dev\` | \`dev\` (no login), \`launchpad_sso\`, \`iap\` |
| \`DATAMAP_CORS_ORIGINS\` | local origins | CORS allow-list override |

**Troubleshooting**

| Symptom | Fix |
|---|---|
| "Network Error" on every call | Run the frontend on \`5173\`, or set \`DATAMAP_CORS_ORIGINS\` |
| "Something went wrong, Try again" | Transient LLM hiccup — click **Retry** |
| AI steps error, no output | Add a valid key to \`.env\` and restart |
| Similarity / Reference "no results" | Expected in standalone (no reference/DART dataset) |

The interactive API spec is at \`/docs\` (Swagger) and \`/openapi.json\`.`}</Md>
        </Card>

        {/* Build bar */}
        <div className="rounded-lg border border-gray-200 bg-white px-4 py-2 text-xs text-slate-500 flex flex-wrap items-center justify-center gap-x-4 gap-y-1">
          <span>DataMap (STTM)</span>
          <span className="text-gray-300">|</span>
          <span>standalone build</span>
          <span className="text-gray-300">|</span>
          <span>no login (dev mode)</span>
          <span className="text-gray-300">|</span>
          <span className="font-medium text-brand-darkblue">Knowledge Base</span>
        </div>
      </main>
    </div>
  );
}
