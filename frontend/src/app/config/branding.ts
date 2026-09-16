/**
 * White-label branding config — single source of truth for product-facing names.
 * Swap these values to re-brand the app without touching component code.
 */
export const BRANDING = {
  /** Product display name (header, browser tab). */
  APP_NAME: "STTM",
  /** Conversational assistant display name. */
  ASSISTANT_NAME: "STTM Assistant",
  /** Prefix used to build workflow role labels, e.g. `${ROLE_LABEL} Feedback`. */
  ROLE_LABEL: "STTM",
} as const;
