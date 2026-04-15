"""Federal Judicial Center — public Article III / federal judicial biographical CSV."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

import httpx

from adapters.base import AdapterResponse, AdapterResult, BaseAdapter, apply_collision_rule
from adapters.courtlistener import _JURISDICTION_COURT_IDS, courtlistener_court_ids_from_jurisdiction

JUDGES_CSV_URL = "https://www.fjc.gov/sites/default/files/history/judges.csv"
HISTORY_PAGE_URL = "https://www.fjc.gov/history/judges"
SOURCE_LABEL = "FJC"
CACHE_FILENAME = "fjc_judges.csv"
CACHE_MAX_AGE = timedelta(days=7)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def default_judges_csv_path() -> Path:
    d = _project_root() / "data" / ".cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / CACHE_FILENAME


def fjc_court_name_hints(jurisdiction: str | None) -> list[str]:
    """Substrings to match against FJC \"Court Name (n)\" columns."""
    j = (jurisdiction or "").strip().lower()
    hints: list[str] = []
    if len(j) > 2:
        hints.append(j)
    for needles, _cid in _JURISDICTION_COURT_IDS:
        if any(n in j for n in needles):
            hints.extend(needles)
    # de-dupe, longest first for scoring
    seen: set[str] = set()
    out: list[str] = []
    for h in sorted({x.strip() for x in hints if len(x.strip()) > 2}, key=len, reverse=True):
        k = h.lower()
        if k not in seen:
            seen.add(k)
            out.append(h.lower())
    return out


def _normalize_name(s: str) -> str:
    s = re.sub(r"[,]+", " ", (s or "").strip())
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return " ".join(s.split())


def _strip_suffix_tokens(parts: list[str]) -> list[str]:
    suffixes = {"jr", "sr", "ii", "iii", "iv", "v", "2nd", "2"}
    out = list(parts)
    while out and out[-1].lower().rstrip(".") in suffixes:
        out.pop()
    return out


def _name_tokens(display_name: str) -> list[str]:
    parts = _strip_suffix_tokens(_normalize_name(display_name).split())
    return parts


def name_match_score(query_name: str, row: dict[str, str]) -> float:
    """0..1 fuzzy similarity between query and FJC name fields."""
    first = (row.get("First Name") or "").strip()
    middle = (row.get("Middle Name") or "").strip()
    last = (row.get("Last Name") or "").strip()
    suffix = (row.get("Suffix") or "").strip()
    full = _normalize_name(f"{first} {middle} {last} {suffix}")
    short = _normalize_name(f"{first} {last}")
    q = _normalize_name(query_name)
    if not q or not last:
        return 0.0
    r_full = SequenceMatcher(None, q, full).ratio() if full else 0.0
    r_short = SequenceMatcher(None, q, short).ratio() if short else 0.0
    # Strong signal: last name appears in query and first initial or full first matches
    qtok = q.split()
    bonus = 0.0
    if last.lower() in q and first:
        if first.lower() in q or (qtok and qtok[0] == first[0].lower()):
            bonus = 0.08
    return min(1.0, max(r_full, r_short) + bonus)


def court_match_score(row: dict[str, str], hints: list[str]) -> float:
    if not hints:
        return 0.0
    best = 0.0
    for i in range(1, 7):
        cn = (row.get(f"Court Name ({i})") or "").strip().lower()
        if not cn:
            continue
        for h in hints:
            if h in cn or cn in h:
                best = max(best, SequenceMatcher(None, h, cn).ratio())
    return best


def iter_csv_rows(text: str) -> list[dict[str, str]]:
    """Parse judges CSV text into dict rows (all string values)."""
    f = io.StringIO(text)
    r = csv.DictReader(f)
    rows: list[dict[str, str]] = []
    for row in r:
        if not row:
            continue
        rows.append({k: (v or "").strip() for k, v in row.items() if k})
    return rows


def ensure_judges_csv(
    path: Path | None = None,
    *,
    now: datetime | None = None,
    fetch: Callable[[str], bytes] | None = None,
) -> Path:
    """
    Ensure a local judges.csv exists and is refreshed at most every CACHE_MAX_AGE.
    """
    p = path or default_judges_csv_path()
    now = now or datetime.now(timezone.utc)
    fetch = fetch or (lambda url: httpx.get(url, timeout=120.0, follow_redirects=True).content)
    need = True
    if p.is_file():
        mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        if now - mtime < CACHE_MAX_AGE:
            need = False
    if need:
        data = fetch(JUDGES_CSV_URL)
        p.write_bytes(data)
    return p


def find_best_judge_matches(
    rows: list[dict[str, str]],
    query_name: str,
    court_hints: list[str],
    *,
    min_name: float = 0.82,
    court_weight: float = 0.12,
) -> tuple[list[dict[str, str]], float]:
    """
    Return rows tied within0.02 of the best combined score (name + court), best first.
    """
    scored: list[tuple[float, dict[str, str]]] = []
    for row in rows:
        ns = name_match_score(query_name, row)
        if ns < min_name:
            continue
        cs = court_match_score(row, court_hints)
        combined = ns + (cs * court_weight if court_hints else 0.0)
        scored.append((combined, row))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return [], 0.0
    top_score = scored[0][0]
    close = [row for s, row in scored if s >= top_score - 0.02]
    return close, top_score


def _fmt_date_parts(row: dict[str, str], idx: int) -> str:
    parts = []
    for key in (
        f"Recess Appointment Date ({idx})",
        f"Nomination Date ({idx})",
        f"Confirmation Date ({idx})",
        f"Commission Date ({idx})",
    ):
        v = (row.get(key) or "").strip()
        if v:
            parts.append(f"{key.split(' (')[0]}: {v}")
    return "; ".join(parts)


def _collect_education(row: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for i in range(1, 6):
        sch = (row.get(f"School ({i})") or "").strip()
        deg = (row.get(f"Degree ({i})") or "").strip()
        yr = (row.get(f"Degree Year ({i})") or "").strip()
        if sch or deg or yr:
            lines.append(" ".join(x for x in (sch, deg, yr) if x).strip())
    return lines


def _collect_other_benches(row: dict[str, str]) -> list[str]:
    out: list[str] = []
    for i in range(1, 5):
        v = (row.get(f"Other Federal Judicial Service ({i})") or "").strip()
        if v:
            out.append(v)
    return out


def _build_body(row: dict[str, str]) -> str:
    lines: list[str] = []
    first = (row.get("First Name") or "").strip()
    middle = (row.get("Middle Name") or "").strip()
    last = (row.get("Last Name") or "").strip()
    suf = (row.get("Suffix") or "").strip()
    legal = " ".join(x for x in (first, middle, last, suf) if x).strip()
    lines.append(f"Full name: {legal}")
    if middle or suf:
        lines.append(f"Name components (incl. middle / suffix): {first} | {middle} | {last} | {suf}")

    for i in range(1, 7):
        ct = (row.get(f"Court Type ({i})") or "").strip()
        cn = (row.get(f"Court Name ({i})") or "").strip()
        title = (row.get(f"Appointment Title ({i})") or "").strip()
        seat = (row.get(f"Seat ID ({i})") or "").strip()
        if not cn and not ct:
            continue
        lines.append(f"Court assignment ({i}): {cn} ({ct}) — {title}".strip())
        if seat:
            lines.append(f"  Seat ID: {seat}")
        ap = (row.get(f"Appointing President ({i})") or "").strip()
        if ap:
            party = (row.get(f"Party of Appointing President ({i})") or "").strip()
            lines.append(f"  Appointing president: {ap}" + (f" ({party})" if party else ""))
        dates = _fmt_date_parts(row, i)
        if dates:
            lines.append(f"  Appointment / commission: {dates}")
        oath = (row.get(f"Commission Date ({i})") or "").strip()
        if oath:
            lines.append(f"  Commission (oath) date: {oath}")
        term = (row.get(f"Termination ({i})") or "").strip()
        tdate = (row.get(f"Termination Date ({i})") or "").strip()
        if term or tdate:
            lines.append(f"  Termination: {term}" + (f" — {tdate}" if tdate else ""))

    other = _collect_other_benches(row)
    if other:
        lines.append("Prior / other federal judicial offices:")
        lines.extend(f"  • {x}" for x in other)

    edu = _collect_education(row)
    if edu:
        lines.append("Education:")
        lines.extend(f"  • {e}" for e in edu)

    career = (row.get("Professional Career") or "").strip()
    if career:
        lines.append(f"Career before bench: {career}")

    other_nom = (row.get("Other Nominations/Recess Appointments") or "").strip()
    if other_nom:
        lines.append(f"Other nominations / recess appointments: {other_nom}")

    birth_y = (row.get("Birth Year") or "").strip()
    birth_c = (row.get("Birth City") or "").strip()
    birth_s = (row.get("Birth State") or "").strip()
    if birth_y or birth_c or birth_s:
        lines.append(f"Birth: {birth_c} {birth_s} {birth_y}".strip())

    lines.append(f"Official FJC judge directory (context): {HISTORY_PAGE_URL}")
    lines.append(f"Row source (bulk open data): {JUDGES_CSV_URL}")

    return "\n".join(lines)


def row_to_adapter_result(row: dict[str, str], *, collision_count: int) -> AdapterResult:
    first = (row.get("First Name") or "").strip()
    last = (row.get("Last Name") or "").strip()
    nid = (row.get("nid") or "").strip()
    title = f"FJC biographical record: {first} {last}".strip()
    body = _build_body(row)
    raw: dict[str, Any] = {
        "fjc_nid": nid,
        "fjc_jid": (row.get("jid") or "").strip(),
        "epistemic_level": "VERIFIED",
        "classification_basis": "adjudicated_record",
    }
    # Primary commission date for timeline sorting (first slot)
    de = None
    for i in range(1, 7):
        cd = (row.get(f"Commission Date ({i})") or "").strip()
        if cd:
            de = cd[:10] if len(cd) >= 10 else cd
            break
    res = AdapterResult(
        source_name=SOURCE_LABEL,
        source_url=JUDGES_CSV_URL,
        entry_type="court_record",
        title=title,
        body=body,
        date_of_event=de,
        confidence="confirmed",
        raw_data=raw,
        matched_name=f"{first} {last}".strip(),
        collision_count=collision_count,
        collision_set=[],
    )
    apply_collision_rule(res)
    return res


class FJCBiographicalAdapter(BaseAdapter):
    """Bulk CSV from FJC; weekly local file cache."""

    source_name = SOURCE_LABEL
    court_ids: list[str]
    jurisdiction_text: str
    csv_path: Path | None
    _now: datetime | None
    _fetch: Callable[[str], bytes] | None

    def __init__(self) -> None:
        self.court_ids = []
        self.jurisdiction_text = ""
        self.csv_path = None
        self._now = None
        self._fetch = None

    def _parse_query(self, query: str) -> str:
        if "|" in query:
            return query.split("|", 1)[0].strip()
        return (query or "").strip()

    def _search_sync(self, query: str, _query_type: str) -> AdapterResponse:
        try:
            path = ensure_judges_csv(
                self.csv_path,
                now=self._now,
                fetch=self._fetch,
            )
            text = path.read_text(encoding="utf-8", errors="replace")
            rows = iter_csv_rows(text)
        except (httpx.HTTPError, httpx.RequestError, OSError) as e:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error=f"FJC CSV fetch/read failed: {e!s}",
                error_kind="network",
            )
        except Exception as e:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error=f"FJC processing error: {e!s}",
                error_kind="processing",
            )

        name_q = self._parse_query(query)
        hints = fjc_court_name_hints(self.jurisdiction_text)
        if not hints and self.court_ids:
            # Map CourtListener ids to jurisdiction needles where possible
            j = (self.jurisdiction_text or "").lower()
            hints = fjc_court_name_hints(j)

        matches, _top = find_best_judge_matches(rows, name_q, hints)
        if not matches:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=True,
                empty_success=True,
                parse_warning=f"No FJC judge row matched {name_q!r} (court hints: {hints!r}).",
                result_hash=hashlib.sha256(b"none").hexdigest()[:32],
            )

        uniq = {(r.get("nid") or r.get("jid") or "").strip() for r in matches}
        uniq.discard("")
        collision_count = len(matches) if len(matches) > 1 else 1
        if len(uniq) > 1:
            collision_count = max(collision_count, len(uniq))

        primary = matches[0]
        res = row_to_adapter_result(primary, collision_count=collision_count)
        rh = hashlib.sha256(
            "|".join(sorted({(r.get("nid") or r.get("jid") or "") for r in matches})).encode()
        ).hexdigest()[:40]
        return AdapterResponse(
            source_name=self.source_name,
            query=query,
            results=[res],
            found=True,
            result_hash=rh,
            parse_warning=None if collision_count <= 1 else "Multiple FJC rows tied; confidence reduced.",
        )

    async def search(self, query: str, query_type: str = "person") -> AdapterResponse:
        return await asyncio.to_thread(self._search_sync, query, query_type)


__all__ = [
    "FJCBiographicalAdapter",
    "JUDGES_CSV_URL",
    "HISTORY_PAGE_URL",
    "default_judges_csv_path",
    "ensure_judges_csv",
    "find_best_judge_matches",
    "fjc_court_name_hints",
    "iter_csv_rows",
    "name_match_score",
]
