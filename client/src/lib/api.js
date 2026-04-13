/** Resolve API path: env override, else same-origin (or dev proxy). */
export function apiUrl(path) {
  const p = path.startsWith("/") ? path : `/${path}`;
  const envBase = (import.meta.env.VITE_OPEN_CASE_API_BASE || "").replace(/\/$/, "");
  if (envBase) return `${envBase}${p}`;
  return p;
}

export function apiHeaders() {
  const key = import.meta.env.VITE_OPEN_CASE_API_KEY;
  const h = { Accept: "application/json" };
  if (key) h.Authorization = `Bearer ${key}`;
  return h;
}
