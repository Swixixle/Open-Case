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

export async function fetchSubjectsSearch(name, filters = {}) {
  const q = new URLSearchParams({ name: name.trim() });
  if (filters.subject_type) q.set("subject_type", filters.subject_type);
  if (filters.government_level) q.set("government_level", filters.government_level);
  if (filters.branch) q.set("branch", filters.branch);
  const res = await fetch(apiUrl(`/api/v1/subjects/search?${q}`), {
    headers: apiHeaders(),
  });
  if (!res.ok) return null;
  return res.json();
}

export async function fetchCasesList(params = {}) {
  const q = new URLSearchParams();
  if (params.government_level) q.set("government_level", params.government_level);
  if (params.branch) q.set("branch", params.branch);
  if (params.subject_type) q.set("subject_type", params.subject_type);
  if (params.limit != null) q.set("limit", String(params.limit));
  const res = await fetch(apiUrl(`/api/v1/cases?${q}`), { headers: apiHeaders() });
  if (!res.ok) return null;
  return res.json();
}

export async function fetchCaseReport(caseId) {
  const res = await fetch(apiUrl(`/api/v1/cases/${encodeURIComponent(caseId)}/report`), {
    headers: apiHeaders(),
  });
  if (res.status === 401 || res.status === 403 || res.status === 404) return null;
  if (!res.ok) return null;
  return res.json();
}
