import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { sttmNav } from "../utils/sttmRoutes";

import {
  deleteAppSession,
  getAppSessionOpenRoute,
  getProfilingRoute,
  listAppSessions,
  renameAppSession,
  selectAppSession,
  type AppSessionItem,
} from "../end-points/appSessionsApi";
import {
  deleteHL7Session,
  listHL7Sessions,
  type HL7SessionListItem,
} from "../end-points/hl7Api";
import { getCurrentAppSessionId, onSessionChanged } from "../utils/appSessionStorage";

const formatDate = (value: string | null) => (value ? new Date(value).toLocaleString() : "Not available");

export default function Sessions() {
  const navigate = useNavigate();
  const [sessions, setSessions] = useState<AppSessionItem[]>([]);
  const [hl7Sessions, setHl7Sessions] = useState<HL7SessionListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(getCurrentAppSessionId());

  const refresh = async () => {
    try {
      const data = await listAppSessions();
      setSessions(data);
      setError(null);
      setCurrentSessionId(getCurrentAppSessionId());
      listHL7Sessions()
        .then((hl7) => setHl7Sessions(hl7))
        .catch(() => setHl7Sessions([]));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch sessions");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    return onSessionChanged(() => {
      setCurrentSessionId(getCurrentAppSessionId());
      refresh();
    });
  }, []);

  const handleSelect = async (sessionId: string) => {
    try {
      const detail = await selectAppSession(sessionId);
      navigate(sttmNav(getAppSessionOpenRoute(detail)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to open session");
    }
  };

  const handleOpenWorkflow = async (sessionId: string, route: "profiling" | "mapping" | "extract") => {
    try {
      const detail = await selectAppSession(sessionId);
      let target = "/extract";
      if (route === "mapping") target = "/mapping?resume=1";
      else if (route === "profiling") target = getProfilingRoute(detail);
      navigate(sttmNav(target));
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to open ${route}`);
    }
  };

  const handleRename = async (session: AppSessionItem) => {
    const nextTitle = globalThis.prompt("Rename session", session.title);
    if (!nextTitle || nextTitle.trim() === session.title) return;
    try {
      await renameAppSession(session.id, nextTitle.trim());
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to rename session");
    }
  };

  const handleDelete = async (session: AppSessionItem) => {
    const confirmed = globalThis.confirm(`Delete session "${session.title}"?`);
    if (!confirmed) return;
    try {
      await deleteAppSession(session.id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete session");
    }
  };

  const handleDeleteHL7 = async (session: HL7SessionListItem) => {
    const confirmed = globalThis.confirm(
      `Delete HL7 analysis ${session.hl7_session_id.slice(0, 8)}… including its mapping package and published versions?`
    );
    if (!confirmed) return;
    try {
      await deleteHL7Session(session.hl7_session_id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete HL7 analysis");
    }
  };

  const hl7StatusChip = (status: string) => {
    if (status.startsWith("published")) return "bg-emerald-50 text-emerald-700 border-emerald-200";
    if (status === "in review") return "bg-amber-50 text-amber-700 border-amber-200";
    return "bg-sky-50 text-sky-700 border-sky-200";
  };

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-xl font-bold mb-1 text-brand-darkblue">Sessions</h2>
          <p className="text-sm text-gray-500">Select, rename, or delete your sessions.</p>
        </div>
      </div>

      {loading && <div className="text-sm text-gray-500">Loading sessions...</div>}
      {error && <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      {!loading && !error && (
        <div className="space-y-4">
          {sessions.length === 0 && (
            <div className="rounded-lg border border-dashed border-gray-300 p-6 text-sm text-gray-500">
              No sessions yet.
            </div>
          )}
          {sessions.map((session) => {
            const isActive = session.id === currentSessionId;
            return (
              <div
                key={session.id}
                className={[
                  "rounded-lg border p-4 shadow-sm transition-colors",
                  isActive ? "border-teal-200 bg-brand-surface" : "border-gray-200 bg-white",
                ].join(" ")}
              >
                <div className="flex items-start justify-between gap-4">
                  <button className="text-left flex-1 cursor-pointer" onClick={() => handleSelect(session.id)}>
                    <div className="text-base font-semibold text-brand-darkblue">{session.title}</div>
                    <div className="text-xs text-gray-500 mt-1">{session.id}</div>
                    <div className="mt-3 flex gap-4 text-xs text-gray-600">
                      {session.id.startsWith("extract_") ? (
                        <span>{session.current_extract_run_id ? "Extract saved" : "No extract run"}</span>
                      ) : session.hl7 ? (
                        <>
                          <span>HL7 profiled ({session.hl7.messages_parsed} messages)</span>
                          <span>
                            HL7 mapping: {session.hl7.mappings_approved}/{session.hl7.mappings_total} approved
                            {session.hl7.latest_version != null && ` · published v${session.hl7.latest_version}`}
                          </span>
                        </>
                      ) : (
                        <>
                          <span>{session.current_profiling_run_id ? "Profiling saved" : "No profiling run"}</span>
                          <span>{session.current_mapping_run_id ? "Mapping saved" : "No mapping run"}</span>
                        </>
                      )}
                      <span>Updated {formatDate(session.updated_at)}</span>
                    </div>
                  </button>
                  <div className="flex flex-wrap items-start justify-end gap-2">
                    {session.hl7 && (
                      <>
                        <button
                          onClick={() => navigate(sttmNav(`/hl7/${session.hl7!.hl7_session_id}`))}
                          className="rounded-md border border-teal-200 bg-brand-surface px-3 py-1.5 text-sm text-font-blue hover:bg-teal-100 cursor-pointer"
                        >
                          Open HL7 Profile
                        </button>
                        <button
                          onClick={() => navigate(sttmNav(`/hl7/${session.hl7!.hl7_session_id}/review`))}
                          className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-sm text-emerald-700 hover:bg-emerald-100 cursor-pointer"
                        >
                          Open HL7 Mapping
                        </button>
                      </>
                    )}
                    {session.current_extract_run_id && (
                      <button
                        onClick={() => handleOpenWorkflow(session.id, "extract")}
                        className="rounded-md border border-teal-200 bg-brand-surface px-3 py-1.5 text-sm text-font-blue hover:bg-teal-100 cursor-pointer"
                      >
                        Open Extract
                      </button>
                    )}
                    {session.current_profiling_run_id && (
                      <button
                        onClick={() => handleOpenWorkflow(session.id, "profiling")}
                        className="rounded-md border border-teal-200 bg-brand-surface px-3 py-1.5 text-sm text-font-blue hover:bg-teal-100 cursor-pointer"
                      >
                        Open Profiling
                      </button>
                    )}
                    {session.current_mapping_run_id && (
                      <button
                        onClick={() => handleOpenWorkflow(session.id, "mapping")}
                        className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-sm text-emerald-700 hover:bg-emerald-100 cursor-pointer"
                      >
                        Open Mapping
                      </button>
                    )}
                    <button
                      onClick={() => handleRename(session)}
                      className="rounded-md border border-gray-200 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 cursor-pointer"
                    >
                      Rename
                    </button>
                    <button
                      onClick={() => handleDelete(session)}
                      className="rounded-md border border-red-200 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50 cursor-pointer"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {!loading && (
        <div className="mt-10">
          <div className="mb-4">
            <h2 className="text-xl font-bold mb-1 text-brand-darkblue">HL7 analyses</h2>
            <p className="text-sm text-gray-500">
              Message-based uploads. Profiling runs instantly at upload; the status tracks the
              mapping review and published package versions.
            </p>
          </div>

          {hl7Sessions.length === 0 && (
            <div className="rounded-lg border border-dashed border-gray-300 p-6 text-sm text-gray-500">
              No HL7 analyses yet. Upload .hl7 files on the Upload page to start one.
            </div>
          )}

          <div className="space-y-4">
            {hl7Sessions.map((session) => (
              <div
                key={session.hl7_session_id}
                className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm"
              >
                <div className="flex items-start justify-between gap-4">
                  <button
                    className="text-left flex-1 cursor-pointer"
                    onClick={() => navigate(sttmNav(`/hl7/${session.hl7_session_id}`))}
                  >
                    <div className="flex items-center gap-3">
                      <span className="text-base font-semibold text-brand-darkblue">
                        {Object.keys(session.message_types).join(", ") || "HL7"}
                      </span>
                      <span
                        className={`text-[11px] font-semibold px-2 py-0.5 rounded border ${hl7StatusChip(session.status)}`}
                      >
                        {session.status}
                      </span>
                    </div>
                    <div className="text-xs text-gray-500 mt-1">{session.hl7_session_id}</div>
                    <div className="mt-3 flex flex-wrap gap-4 text-xs text-gray-600">
                      <span>
                        {session.messages_parsed} message(s)
                        {session.messages_failed > 0 && `, ${session.messages_failed} failed`}
                      </span>
                      {session.z_segment_names.length > 0 && (
                        <span>Z-segments: {session.z_segment_names.join(", ")}</span>
                      )}
                      <span>
                        Mappings: {session.mappings_approved}/{session.mappings_total} approved
                        {session.mappings_pending > 0 && `, ${session.mappings_pending} pending`}
                      </span>
                      <span>Created {formatDate(session.created_at)}</span>
                    </div>
                  </button>
                  <div className="flex flex-wrap items-start justify-end gap-2">
                    <button
                      onClick={() => navigate(sttmNav(`/hl7/${session.hl7_session_id}`))}
                      className="rounded-md border border-teal-200 bg-brand-surface px-3 py-1.5 text-sm text-font-blue hover:bg-teal-100 cursor-pointer"
                    >
                      Open Profile
                    </button>
                    <button
                      onClick={() => navigate(sttmNav(`/hl7/${session.hl7_session_id}/review`))}
                      className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-sm text-emerald-700 hover:bg-emerald-100 cursor-pointer"
                    >
                      Open Mapping Review
                    </button>
                    <button
                      onClick={() => handleDeleteHL7(session)}
                      className="rounded-md border border-red-200 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50 cursor-pointer"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
