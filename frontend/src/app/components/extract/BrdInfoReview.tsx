import { useState, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import SimpleMDE from "react-simplemde-editor";
import type { Options } from "easymde";
import EasyMDE from "easymde";
import { type RequirementLayer } from "../../end-points/extract/extractApi";
import { BRD_DEFAULTS } from "../../config/brdDefaults";

interface Props {
  readonly layer: RequirementLayer;
  readonly sessionId: string;
  readonly reviewStatus: "idle" | "approved" | "rejected";
  readonly approveLoading: boolean;
  readonly rejectLoading: boolean;
  readonly disabled?: boolean;
  readonly onApprove: () => void;
  readonly onReject: (comment: string) => void;
  readonly onUpdateAndApprove: (layer: RequirementLayer) => void;
  readonly onReactivate?: () => void;
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function isMarkdown(val: string): boolean {
  return /^#{1,6} |\*\*|__|\|.+\||^[-*+] |^\d+\. |^> |```/m.test(val);
}

function isHtml(val: string): boolean {
  return /^\s*<(ul|ol|p|br)/.test(val);
}

// Insert a blank line before any table row that directly follows a non-blank,
// non-table line so remarkGfm parses it as a block-level table.
function normalizeMarkdown(val: string): string {
  return val
    .replaceAll(/(\*[^\n]+)\n(\|)/g, (_, listItem, pipe) => `${listItem}\n\n${pipe}`)
    .replaceAll(/([^\n|-])\n(\|)/g, (_, before, pipe) => `${before}\n\n${pipe}`);
}

const MD_COMPONENTS: React.ComponentProps<typeof ReactMarkdown>["components"] = {
  table: ({ children }) => <table className="border-collapse text-xs w-full">{children}</table>,
  th: ({ children }) => <th className="border border-gray-300 bg-gray-100 px-3 py-2 text-left font-semibold">{children}</th>,
  td: ({ children }) => <td className="border border-gray-300 px-3 py-2">{children}</td>,
  ul: ({ children }) => <ul className="list-disc list-inside space-y-0.5">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal list-inside space-y-0.5">{children}</ol>,
  p: ({ children }) => <p className="mb-1 last:mb-0">{children}</p>,
};

function toLabel(key: string) {
  return key.replaceAll("_", " ").split(" ").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

const MDE_OPTIONS: Options = {
  spellChecker: false,
  toolbar: ["bold", "italic", "strikethrough", "|", "heading", "|", "unordered-list", "ordered-list", "|", "table", "code", "|", "preview"],
  minHeight: "120px",
  status: false,
};

function enablePreview(instance: EasyMDE) {
  setTimeout(() => EasyMDE.togglePreview(instance), 0);
}

// ── Reject Modal ─────────────────────────────────────────────────────────────

function RejectModal({
  loading,
  onConfirm,
  onCancel,
}: {
  readonly loading: boolean;
  readonly onConfirm: (comment: string) => void;
  readonly onCancel: () => void;
}) {
  const [comment, setComment] = useState("");
  const [touched, setTouched] = useState(false);
  const hasError = touched && !comment.trim();

  const handleConfirm = () => {
    setTouched(true);
    if (!comment.trim()) return;
    onConfirm(comment.trim());
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md mx-4 p-6">
        <h3 className="text-base font-bold text-brand-darkblue mb-1">Reject Extraction</h3>
        <p className="text-xs text-gray-500 mb-4">Please provide a reason for rejection before confirming.</p>

        <div className="flex flex-col gap-1 mb-5">
          <label className="block text-sm font-medium text-gray-700 mb-1" htmlFor="reject-comment">
            Rejection Comment <span className="text-red-500">*</span>
          </label>
          <textarea
            id="reject-comment"
            rows={4}
            value={comment}
            onChange={(e) => { setComment(e.target.value); setTouched(true); }}
            placeholder="Enter reason for rejection…"
            className={`w-full px-3 py-2 border rounded-md bg-white text-xs resize-none focus:outline-none focus:ring-2 ${
              hasError ? "border-red-400 focus:ring-red-400" : "border-gray-300 focus:ring-brand-primary"
            }`}
          />
          {hasError && <p className="text-xs text-red-600 mt-1">Rejection comment is required.</p>}
        </div>

        <div className="flex justify-end gap-3">
          <button
            type="button"
            disabled={loading}
            onClick={onCancel}
            className="px-4 py-2 text-sm border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50 transition-colors cursor-pointer"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={loading}
            onClick={handleConfirm}
            className="px-4 py-2 text-sm bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors cursor-pointer"
          >
            {loading ? "Rejecting…" : "Confirm Reject"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Read-only section ────────────────────────────────────────────────────────

function ReadSection({ title, data }: { readonly title: string; readonly data: Record<string, unknown> }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden mb-3">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex justify-between items-center px-4 py-3 bg-gray-50 hover:bg-gray-100 text-sm font-bold text-brand-darkblue cursor-pointer"
      >
        {title}
        <span className="text-gray-400 font-normal">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="p-4 grid grid-cols-2 gap-6">
          {Object.entries(data).map(([key, val]) => {
            if (isObject(val)) {
              return (
                <div key={key} className="col-span-2">
                  <ReadSection title={toLabel(key)} data={val} />
                </div>
              );
            }
            const strVal = isObject(val) ? "" : String(val ?? "");
            let fieldContent: React.ReactNode;
            if (isHtml(strVal)) {
              fieldContent = (
                <div
                  className="w-full px-3 py-2 border border-gray-200 rounded-md bg-white text-xs text-gray-800 min-h-8 [&_ul]:list-disc [&_ul]:list-inside [&_ol]:list-decimal [&_ol]:list-inside [&_li]:my-0.5"
                  dangerouslySetInnerHTML={{ __html: strVal }}
                />
              );
            } else if (isMarkdown(strVal)) {
              fieldContent = (
                <div className="w-full px-3 py-2 border border-gray-200 rounded-md bg-white text-xs text-gray-800 min-h-8 prose prose-xs max-w-none overflow-x-auto">
                  <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
                    {normalizeMarkdown(strVal)}
                  </ReactMarkdown>
                </div>
              );
            } else {
              fieldContent = (
                <span className="w-full px-3 py-2 border border-gray-200 rounded-md bg-white text-xs text-gray-800 min-h-8 wrap-break-word">
                  {strVal || <span className="text-gray-300 italic">—</span>}
                </span>
              );
            }
            return (
              <div key={key} className="col-span-1 flex flex-col gap-1">
                <span className="block text-sm font-medium text-gray-700">{toLabel(key)}</span>
                {fieldContent}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Editable section ─────────────────────────────────────────────────────────

function EditSection({
  title,
  data,
  onChange,
}: {
  readonly title: string;
  readonly data: Record<string, unknown>;
  readonly onChange: (updated: Record<string, unknown>) => void;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div className="border border-teal-200 rounded-lg overflow-hidden mb-3">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex justify-between items-center px-4 py-3 bg-brand-surface hover:bg-teal-100 text-sm font-bold text-brand-darkblue cursor-pointer"
      >
        {title}
        <span className="text-teal-300 font-normal">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="p-4 grid grid-cols-2 gap-6">
          {Object.entries(data).map(([key, val]) => {
            if (isObject(val)) {
              return (
                <div key={key} className="col-span-2">
                  <EditSection
                    title={toLabel(key)}
                    data={val as Record<string, unknown>}
                    onChange={(updated) => onChange({ ...data, [key]: updated })}
                  />
                </div>
              );
            }
            return (
              <div key={key} className="col-span-1 flex flex-col gap-1">
                <label className="block text-sm font-medium text-gray-700">{toLabel(key)}</label>
                <div className="mde-sm">
                  <SimpleMDE
                    value={String(val ?? "")}
                    onChange={(v) => onChange({ ...data, [key]: v })}
                    options={MDE_OPTIONS}
                    getMdeInstance={enablePreview}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── General string fields (read / edit) ──────────────────────────────────────

function GeneralSection({
  editMode,
  bsaInput,
  onChange,
}: {
  readonly editMode: boolean;
  readonly bsaInput: string;
  readonly onChange: (key: "bsa_input", val: string) => void;
}) {
  const [open, setOpen] = useState(true);
  const borderCls = editMode ? "border-teal-200" : "border-gray-200";
  const headerCls = editMode
    ? "bg-brand-surface hover:bg-teal-100 text-brand-darkblue"
    : "bg-gray-50 hover:bg-gray-100 text-brand-darkblue";

  return (
    <div className={`border ${borderCls} rounded-lg overflow-hidden mb-3`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={`w-full flex justify-between items-center px-4 py-3 text-sm font-bold cursor-pointer ${headerCls}`}
      >
        STTM Input
        <span className="text-gray-400 font-normal">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="p-4 grid grid-cols-2 gap-6">
          {(["bsa_input"] as const).map((key) => (
            <div key={key} className="col-span-2 flex flex-col gap-1">
              {/* <label className="block text-sm font-medium text-gray-700">{toLabel(key)}</label> */}
              {editMode ? (
                <div className="mde-sm">
                  <SimpleMDE
                    value={bsaInput}
                    onChange={(v) => onChange(key, v)}
                    options={MDE_OPTIONS}
                    getMdeInstance={enablePreview}
                  />
                </div>
              ) : (
                <div className="w-full px-3 py-2 border border-gray-200 rounded-md bg-white text-xs text-gray-800 min-h-[32px] prose prose-xs max-w-none overflow-x-auto">
                  <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw as never]} components={MD_COMPONENTS}>
                    {normalizeMarkdown(bsaInput || "—")}
                  </ReactMarkdown>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Apply defaults ───────────────────────────────────────────────────────────

function mergeWithDefaults(data: Record<string, unknown>, defaults: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = { ...defaults };
  for (const key of Object.keys(defaults)) {
    const val = data[key];
    const def = defaults[key];
    if (isObject(val) && isObject(def)) {
      result[key] = mergeWithDefaults(val, def);
    } else {
      result[key] = (val !== null && val !== undefined && String(val).trim() !== "") ? val : def;
    }
  }
  // preserve any extra keys from data not in defaults
  for (const key of Object.keys(data)) {
    if (!(key in defaults)) result[key] = data[key];
  }
  return result;
}

function applyDefaults(layer: RequirementLayer): RequirementLayer {
  return {
    ...layer,
    bsa_input: (layer.bsa_input !== null && layer.bsa_input !== undefined && String(layer.bsa_input).trim() !== "")
      ? layer.bsa_input
      : BRD_DEFAULTS.bsa_input,
    requirements: (layer.requirements !== null && layer.requirements !== undefined && String(layer.requirements).trim() !== "")
      ? layer.requirements
      : BRD_DEFAULTS.requirements,
    scope: mergeWithDefaults(layer.scope as unknown as Record<string, unknown>, BRD_DEFAULTS.scope as unknown as Record<string, unknown>) as RequirementLayer["scope"],
    filters_and_parameters: mergeWithDefaults(layer.filters_and_parameters as unknown as Record<string, unknown>, BRD_DEFAULTS.filters_and_parameters as unknown as Record<string, unknown>) as RequirementLayer["filters_and_parameters"],
    file_attributes_mapping: mergeWithDefaults(layer.file_attributes_mapping as unknown as Record<string, unknown>, BRD_DEFAULTS.file_attributes_mapping as unknown as Record<string, unknown>) as RequirementLayer["file_attributes_mapping"],
    file_specs: mergeWithDefaults(layer.file_specs as unknown as Record<string, unknown>, BRD_DEFAULTS.file_specs as unknown as Record<string, unknown>) as RequirementLayer["file_specs"],
    common_rules: mergeWithDefaults(layer.common_rules as unknown as Record<string, unknown>, BRD_DEFAULTS.common_rules as unknown as Record<string, unknown>) as RequirementLayer["common_rules"],
  };
}

// ── Main Component ───────────────────────────────────────────────────────────

export default function BrdInfoReview({
  layer,
  sessionId,
  reviewStatus,
  approveLoading,
  rejectLoading,
  disabled = false,
  onApprove,
  onReject,
  onUpdateAndApprove,
  onReactivate,
}: Props) {
  const [editMode, setEditMode] = useState(false);
  const [edited, setEdited] = useState<RequirementLayer>(() => applyDefaults(layer));
  const [showRejectModal, setShowRejectModal] = useState(false);
  const [reactivated, setReactivated] = useState(false);

  useEffect(() => { setEdited(applyDefaults(layer)); }, [layer]);

  const update = (section: keyof RequirementLayer, value: unknown) =>
    setEdited((prev) => ({ ...prev, [section]: value }));

  const handleCancel = () => { setEdited(layer); setEditMode(false); };

  const handleApprove = () => {
    setReactivated(false);
    onApprove();
  };

  const handleUpdateAndApprove = (updated: RequirementLayer) => {
    setReactivated(false);
    onUpdateAndApprove(updated);
  };

  const handleRejectConfirm = (comment: string) => {
    setShowRejectModal(false);
    onReject(comment);
  };

  const isApproved = reviewStatus === "approved" && !reactivated;
  const isLocked = reviewStatus === "rejected";
  const showReactivatedNote = reviewStatus === "approved" && reactivated && !editMode;

  return (
    <div className="mt-2">
      {/* Header */}
      <div className="mb-1">
        <h3 className="text-base font-bold text-brand-darkblue">Extracted BRD Information</h3>
      </div>
      <p className="text-xs text-gray-400 mb-4">Session: {sessionId}</p>

      {/* Sections */}
      {editMode ? (
        <>
          <EditSection
            title="Scope"
            data={edited.scope as unknown as Record<string, unknown>}
            onChange={(v) => update("scope", v)}
          />
          <GeneralSection
            editMode
            bsaInput={String(edited.bsa_input ?? "")}
            onChange={(key, val) => setEdited((p) => ({ ...p, [key]: val }))}
          />
          <EditSection title="Filters & Parameters" data={edited.filters_and_parameters as unknown as Record<string, unknown>} onChange={(v) => update("filters_and_parameters", v)} />
          <EditSection title="File Attributes Mapping" data={edited.file_attributes_mapping as unknown as Record<string, unknown>} onChange={(v) => update("file_attributes_mapping", v)} />
          <EditSection title="File Specs" data={edited.file_specs as unknown as Record<string, unknown>} onChange={(v) => update("file_specs", v)} />
          <EditSection title="Common Rules" data={edited.common_rules as unknown as Record<string, unknown>} onChange={(v) => update("common_rules", v)} />
        </>
      ) : (
        <>
          <ReadSection title="Scope" data={edited.scope as unknown as Record<string, unknown>} />
          <GeneralSection
            editMode={false}
            bsaInput={String(edited.bsa_input ?? "")}
            onChange={() => {}}
          />
          <ReadSection title="Filters & Parameters" data={edited.filters_and_parameters as unknown as Record<string, unknown>} />
          <ReadSection title="File Attributes Mapping" data={edited.file_attributes_mapping as unknown as Record<string, unknown>} />
          <ReadSection title="File Specs" data={edited.file_specs as unknown as Record<string, unknown>} />
          <ReadSection title="Common Rules" data={edited.common_rules as unknown as Record<string, unknown>} />
        </>
      )}

      {/* Action bar */}
      <div className="flex items-center gap-3 mt-4 pt-4 border-t border-gray-200">
        {showReactivatedNote && (
          <p className="text-xs text-amber-600 italic">Note: Re-approve to apply changes.</p>
        )}
        {!isLocked && !editMode && (
          <>
            <button
              type="button"
              disabled={disabled}
              onClick={() => { setEditMode(true); setReactivated(true); onReactivate?.(); }}
              className="px-4 py-2 text-sm border border-brand-darkblue text-brand-darkblue rounded-lg hover:bg-brand-surface disabled:opacity-50 disabled:cursor-not-allowed transition-colors cursor-pointer"
            >
              Edit
            </button>
            <button
              type="button"
              disabled={approveLoading || isApproved || disabled}
              onClick={handleApprove}
              className="px-4 py-2 text-sm bg-brand-primary text-white rounded-lg hover:bg-brand-primary-hover disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors cursor-pointer"
            >
              {approveLoading ? "Approving..." : isApproved ? "Approved ✓" : "Approve"}
            </button>
            <button
              type="button"
              disabled={rejectLoading || disabled}
              onClick={() => setShowRejectModal(true)}
              className="px-4 py-2 text-sm border border-red-500 text-red-600 rounded-lg hover:bg-red-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors cursor-pointer"
            >
              {rejectLoading ? "Rejecting..." : "Reject"}
            </button>

          </>
        )}

        {!isLocked && editMode && (
          <>
            <button
              type="button"
              disabled={approveLoading || disabled}
              onClick={() => { handleUpdateAndApprove(edited); setEditMode(false); }}
              className="px-4 py-2 text-sm bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors cursor-pointer"
            >
              {approveLoading ? "Saving..." : "Update & Approve"}
            </button>
            <button
              type="button"
              disabled={approveLoading || disabled}
              onClick={handleCancel}
              className="px-4 py-2 text-sm border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors cursor-pointer"
            >
              Cancel
            </button>
          </>
        )}

        {isLocked && (
          <p className="text-sm text-gray-500">
            This extraction has been{" "}<span className="font-medium">{reviewStatus}</span>.
          </p>
        )}
      </div>

      {showRejectModal && (
        <RejectModal
          loading={rejectLoading}
          onConfirm={handleRejectConfirm}
          onCancel={() => setShowRejectModal(false)}
        />
      )}
    </div>
  );
}
