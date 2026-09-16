import { STTM_APP_BASE_PATH } from "../../constants";

const ABSOLUTE_URL_PATTERN = /^(?:[a-z][a-z0-9+.-]*:|\/\/)/i;

/** Path relative to app root (e.g. /upload -> /upload). */
export function sttmRelativePath(pathname: string): string {
  if (STTM_APP_BASE_PATH && pathname.startsWith(STTM_APP_BASE_PATH)) {
    const rest = pathname.slice(STTM_APP_BASE_PATH.length);
    return rest || "/";
  }
  return pathname;
}

/** Full path for STTM routes in standalone mode (no /sttm/app prefix). */
export function sttmNav(path = "/"): string {
  const value = path.trim() || "/";

  if (ABSOLUTE_URL_PATTERN.test(value)) {
    return value;
  }

  if (value.startsWith("#")) {
    return value;
  }

  if (value === "/") {
    return "/dashboard";
  }

  return `/${value.replace(/^\/+/, "")}`;
}
