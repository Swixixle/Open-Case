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
  if (filters.state) q.set("state", filters.state);
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

/**
 * @param {string} caseId
 * @param {{ signal?: AbortSignal, demoInternalSignals?: boolean } | undefined} opts
 */
export async function fetchCaseReport(caseId, opts) {
  const q = new URLSearchParams();
  if (opts?.demoInternalSignals) q.set("demo_internal_signals", "1");
  const qs = q.toString();
  const url = apiUrl(
    `/api/v1/cases/${encodeURIComponent(caseId)}/report${qs ? `?${qs}` : ""}`,
  );
  if (import.meta.env.DEV) {
    // eslint-disable-next-line no-console
    console.info("[open-case] fetchCaseReport start", { caseId, url: url.slice(0, 80) });
  }
  const res = await fetch(url, {
    headers: apiHeaders(),
    signal: opts?.signal,
  });
  if (res.status === 401 || res.status === 403 || res.status === 404) {
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.info("[open-case] fetchCaseReport: no body", { status: res.status });
    }
    return null;
  }
  if (!res.ok) {
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.warn("[open-case] fetchCaseReport: HTTP", res.status, res.statusText);
    }
    return null;
  }
  try {
    const data = await res.json();
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.info("[open-case] fetchCaseReport: parsed", {
        hasSignals: Array.isArray(data?.signals),
        signalCount: data?.signals?.length,
        hasPatternAlerts: Array.isArray(data?.pattern_alerts),
      });
    }
    return data;
  } catch (e) {
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.error("[open-case] fetchCaseReport: JSON parse failed", e);
    }
    return null;
  }
}

/** Newest investigate case for a bioguide id (404 if none). */
export async function fetchCaseLookupByBioguide(bioguideId) {
  const bg = encodeURIComponent(bioguideId || "");
  const res = await fetch(apiUrl(`/api/v1/cases/lookup-by-bioguide/${bg}`), {
    headers: apiHeaders(),
  });
  if (res.status === 404) return null;
  if (!res.ok) return null;
  try {
    return await res.json();
  } catch {
    return null;
  }
}

/** GET stored AI investigation summary, if any. */
export async function fetchCaseNarrative(caseId) {
  const res = await fetch(apiUrl(`/api/v1/cases/${encodeURIComponent(caseId)}/narrative`), {
    headers: apiHeaders(),
  });
  if (res.status === 404) return { _status: "none" };
  if (!res.ok) {
    const text = await res.text();
    let msg = text || res.statusText;
    try {
      const j = text ? JSON.parse(text) : null;
      if (j && typeof j.detail === "string") msg = j.detail;
      else if (Array.isArray(j?.detail) && j.detail[0]?.msg) msg = j.detail[0].msg;
    } catch {
      /* keep raw */
    }
    return { _status: "error", _message: msg };
  }
  try {
    return { _status: "ok", ...(await res.json()) };
  } catch {
    return { _status: "error", _message: "Invalid response" };
  }
}

/** POST generate AI summary; requires VITE_OPEN_CASE_API_KEY. */
export async function synthesizeCaseNarrative(caseId) {
  const res = await fetch(
    apiUrl(`/api/v1/cases/${encodeURIComponent(caseId)}/synthesize-narrative`),
    {
      method: "POST",
      headers: { ...apiHeaders(), "Content-Type": "application/json" },
    }
  );
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text?.slice(0, 2000) || res.statusText };
  }
  if (!res.ok) {
    const detail = data?.detail ?? data;
    const msg =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d) => d?.msg || d).join(" ")
          : res.statusText;
    throw new Error(msg);
  }
  return data;
}

/** Server-routed LLM (Gemini / Claude) for story angles; requires API key + server LLM env. */
export async function fetchStoryAngles(dossier) {
  const res = await fetch(apiUrl("/api/v1/assist/story-angles"), {
    method: "POST",
    headers: { ...apiHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ dossier: dossier || {} }),
  });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text?.slice(0, 500) || res.statusText };
  }
  if (!res.ok) {
    const detail = data?.detail ?? data ?? res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}
