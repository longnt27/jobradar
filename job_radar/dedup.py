"""Cross-source vacancy identity, reviewable matches, and transactional cleanup.

An observation is immutable source evidence, while a vacancy is the role that
the person reviews. Matching never relies on an email address or title alone.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .db import Database, new_id, now
from .ranking import score_job


def normalized(value: str | None) -> str:
    value = unicodedata.normalize("NFKD", (value or "").casefold()).replace("đ", "d")
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def company_key(value: str | None) -> str:
    value = normalized(value)
    value = re.sub(r"\s+careers?$", "", value)
    value = re.sub(r"^(?:cong ty|company)\s+", "", value)
    value = re.sub(r"\s+(?:jsc|joint stock company|co ltd|ltd|limited|inc|corporation)$", "", value).strip()
    return value.replace(" ", "")


def employer_keys(conn: sqlite3.Connection) -> dict[str, str]:
    keys = {}
    for row in conn.execute("SELECT id,name,aliases FROM employers ORDER BY category='discovered',category='custom',created_at,id"):
        for name in [row["name"], *json.loads(row["aliases"] or "[]")]:
            keys.setdefault(company_key(name), row["id"])
    return keys


def posting_key(value: str | None) -> str:
    """Only a specific HTTP posting can be used as shared application evidence."""
    if not value:
        return ""
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return ""
    path = parts.path.rstrip("/")
    query = dict(parse_qsl(parts.query))
    linkedin = re.search(r"/jobs/view/(?:[^/]*-)?(\d+)$", path)
    if "linkedin.com" == parts.hostname.removeprefix("www.") and linkedin:
        return f"linkedin.com/jobs/view/{linkedin[1]}"
    generic = {"", "jobs", "job", "careers", "career", "apply", "jobs search", "job search",
               "alljobs", "latest jobs", "job list", "tuyen dung", "recruitment", "job search"}
    last = normalized(path.split("/")[-1])
    identity_query = any(normalized(k) in {"jobid", "job id", "jid", "requisitionid", "reqid", "job"} for k in query)
    if (last in generic or path.lower().endswith("/job/search")) and not identity_query:
        return ""
    if not path and not identity_query:
        return ""
    # A bare /apply or /jobs page is shared by many unrelated vacancies.
    if len(path.strip("/").split("/")) == 1 and not identity_query:
        if not re.search(r"job|career|tuyen|engineer|developer|nhan-vien|analyst|scientist|pentest", path, re.I):
            return ""
    query = {k: v for k, v in query.items() if not k.lower().startswith("utm_") and
             k.lower() not in {"fbclid", "trk", "trackingid", "refid", "ref", "source", "gh_src", "lang"}}
    return urlunsplit(("https", parts.hostname.removeprefix("www."), path, urlencode(sorted(query.items())), ""))


def career_requisition(url: str, external_id: str) -> tuple[str | None, str]:
    """Announcement role codes are unique within their document, not the host."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    if "jr_role" in query:
        role = query.pop("jr_role")
        announcement = posting_key(urlunsplit(parts._replace(query=urlencode(query))))
        external_id = f"{announcement}#jr_role={role}"
    return parts.hostname, external_id


_TITLE_NOISE = set("hiring recruitment recruiting tuyen dung can tim nhan vien chuyen gia chuyen vien cap eng fluent fluency onsite on site remote hybrid hn hcm hanoi ha noi vietnam viet nam english".split())
_LEVELS = {
    "intern": r"\b(intern|internship|trainee|thuc tap|tts)\b",
    "junior": r"\b(junior|fresher|entry level|new grad)\b",
    "mid": r"\b(middle|mid level|mid)\b",
    "senior": r"\b(senior|sr|cao cap)\b",
    "lead": r"\b(lead|truong nhom)\b",
    "staff": r"\b(staff)\b", "principal": r"\b(principal)\b",
    "manager": r"\b(manager|truong phong)\b", "director": r"\b(director|head)\b",
}
_SPECIALTIES = {
    "frontend": r"\b(frontend|front end)\b", "backend": r"\b(backend|back end)\b",
    "vision": r"\b(computer vision|thi giac|cv|ocr)\b", "language": r"\b(llm|nlp|ngon ngu)\b",
    "data_engineering": r"\b(data engineer|ky su du lieu)\b", "data_science": r"\b(data scientist|khoa hoc du lieu)\b",
    "devops": r"\b(devops|sre)\b", "security": r"\b(security|an ninh|bao mat|pentest|soc)\b",
}
_CITIES = {"hanoi": r"\b(ha noi|hanoi|dong da|cau giay|nam tu liem)\b",
           "hcm": r"\b(ho chi minh|hcm|hcmc|saigon|sai gon)\b",
           "danang": r"\b(da nang|danang)\b"}


def _markers(value: str, markers: dict[str, str]) -> set[str]:
    text = normalized(value)
    return {key for key, pattern in markers.items() if re.search(pattern, text)}


def _title_tokens(value: str) -> set[str]:
    text = normalized(value)
    text = re.sub(r"\b(?:upto|up to)\s+\d+.*$|\b\d+yoe\b", " ", text)
    for phrase, replacement in (("machine learning", "ml"), ("artificial intelligence", "ai"),
                                ("tri tue nhan tao", "ai"), ("ky su du lieu", "data engineer"),
                                ("ky su", "engineer")):
        text = text.replace(phrase, replacement)
    for pattern in _LEVELS.values():
        text = re.sub(pattern, " ", text)
    return set(text.split()) - _TITLE_NOISE


def _content(value: str) -> list[str]:
    lines = value.splitlines()
    useful = []
    for line in lines:
        text = normalized(line)
        if re.match(r"^(?:\d+ |[ivx]+ )?(?:benefits|what we offer|why join|che do|quyen loi|phuc loi)\b", text) and len(" ".join(useful).split()) >= 35:
            break
        if re.match(r"^(?:about us|about the company|ve cong ty|company overview)\b", text):
            continue
        useful.append(text)
    return " ".join(useful).split()[:1800]


def _similarity(left: list[str], right: list[str]) -> tuple[float, float]:
    a, b = Counter(left), Counter(right)
    common = sum((a & b).values())
    union = sum((a | b).values())
    word = common / union if union else 0
    shingles_a = {tuple(left[i:i + 3]) for i in range(len(left) - 2)}
    shingles_b = {tuple(right[i:i + 3]) for i in range(len(right) - 2)}
    common_shingles = len(shingles_a & shingles_b)
    phrase = 2 * common_shingles / (len(shingles_a) + len(shingles_b)) if shingles_a and shingles_b else 0
    return word, phrase


def _date(value: str | None) -> datetime | None:
    try:
        result = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        return result.replace(tzinfo=result.tzinfo or timezone.utc)
    except ValueError:
        return None


def _contacts(value: str) -> set[str]:
    emails = set(re.findall(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", value.casefold()))
    phones = {re.sub(r"\D", "", p) for p in re.findall(r"(?:\+?\d[\d .()-]{7,}\d)", value)}
    return emails | {p for p in phones if len(p) >= 9}


def _place(value: str | None) -> str:
    text = normalized(re.sub(r"\([^)]*\)", "", value or "").split(",")[0])
    text = re.sub(r"\b(?:capital|metropolitan|metro|city|region|area)\b", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return "" if text in {"", "vietnam", "viet nam", "united states", "remote", "worldwide", "global"} else text


def _mode(record: dict) -> str:
    if record.get("work_mode"):
        return normalized(record["work_mode"])
    text = normalized(record.get("location"))
    return "remote" if "remote" in text else "on site" if "on site" in text or "onsite" in text else ""


@dataclass(frozen=True)
class Match:
    automatic: bool
    reason: str
    confidence: float


def _fingerprint(record: dict) -> dict:
    if "_fingerprint" not in record:
        title = record["title"]
        record["_fingerprint"] = {
            "company": company_key(record.get("company")), "title": normalized(title),
            "tokens": _title_tokens(title), "levels": _markers(title, _LEVELS),
            "areas": _markers(title, _SPECIALTIES), "cities": _markers(record.get("location") or "", _CITIES),
            "mode": _mode(record), "place": _place(record.get("location")),
            "date": _date(record.get("published_at")) or _date(record.get("first_seen_at")),
            "numbers": set(re.findall(r"\b\d+\b", title)),
            "codes": set(re.findall(r"\b(?:\d{4}[a-z]{2,}\d+|[a-z]{2,}\d{3,})\b", title.casefold())),
            "urls": {posting_key(u) for u in record.get("urls", [])} - {""},
            "apply": posting_key(record.get("apply_url")),
        }
    return record["_fingerprint"]


def assess(left: dict, right: dict, keys: dict[str, str]) -> Match | None:
    """Return high-confidence identity or a review suggestion; contradictions win."""
    if left["id"] == right["id"] or left.get("protected") or right.get("protected"):
        return None
    fp_a, fp_b = _fingerprint(left), _fingerprint(right)
    if fp_a["title"] in {"join linkedin", "careers", "jobs"} or fp_b["title"] in {"join linkedin", "careers", "jobs"}:
        return None
    a, b = fp_a["company"], fp_b["company"]
    unknown = {"", "facebookpost", "unknownemployer"}
    same_employer = a not in unknown and b not in unknown and (a == b or (keys.get(a) and keys.get(a) == keys.get(b)))
    if not same_employer and a not in unknown and b not in unknown:
        return None
    levels_a, levels_b = fp_a["levels"], fp_b["levels"]
    if levels_a and levels_b and not levels_a & levels_b:
        return None
    if (levels_a == {"intern"}) != (levels_b == {"intern"}):
        return None
    advanced = {"staff", "principal", "lead", "manager", "director"}
    if levels_a and levels_b and (levels_a & advanced) != (levels_b & advanced):
        return None
    areas_a, areas_b = fp_a["areas"], fp_b["areas"]
    if areas_a and areas_b and not areas_a & areas_b:
        return None
    nums_a, nums_b = fp_a["numbers"], fp_b["numbers"]
    if nums_a and nums_b and nums_a != nums_b:
        return None
    codes_a, codes_b = fp_a["codes"], fp_b["codes"]
    if codes_a and codes_b and codes_a != codes_b:
        return None
    cities_a, cities_b = fp_a["cities"], fp_b["cities"]
    if cities_a and cities_b and not cities_a & cities_b:
        return None
    if bool(cities_a) != bool(cities_b) and fp_a["place"] and fp_b["place"]:
        return None
    if not cities_a and not cities_b and fp_a["place"] and fp_b["place"] and fp_a["place"] != fp_b["place"]:
        return None
    modes_a, modes_b = fp_a["mode"], fp_b["mode"]
    if {modes_a, modes_b} == {"remote", "on site"} or {modes_a, modes_b} == {"remote", "onsite"}:
        return None
    titles_a, titles_b = fp_a["tokens"], fp_b["tokens"]
    containment = len(titles_a & titles_b) / min(len(titles_a), len(titles_b)) if titles_a and titles_b else 0
    title_agreement = len(titles_a & titles_b) / len(titles_a | titles_b) if titles_a or titles_b else 0
    title_score = max(containment, SequenceMatcher(None, fp_a["title"], fp_b["title"]).ratio())
    if title_score < 0.65:
        return None
    urls_a, urls_b = fp_a["urls"], fp_b["urls"]
    apply_a, apply_b = fp_a["apply"], fp_b["apply"]
    # Most candidates are already ruled out by employer, role, or location.
    # Parse long descriptions only after those inexpensive checks pass.
    for record, fingerprint in ((left, fp_a), (right, fp_b)):
        if "content" not in fingerprint:
            fingerprint["content"] = _content(record.get("description") or "")
    texts_a, texts_b = fp_a["content"], fp_b["content"]
    word, phrase = _similarity(texts_a, texts_b)
    if same_employer and urls_a & urls_b:
        return Match(True, "same_url", 1.0)
    if same_employer and ((apply_a and apply_a in urls_b) or (apply_b and apply_b in urls_a)):
        # Some existing collectors picked a neighbouring role's Apply link.
        # Compatible titles and substantial shared content must corroborate it.
        if title_agreement >= 0.8 and word >= 0.65 and phrase >= 0.5 and min(len(texts_a), len(texts_b)) >= 35:
            return Match(True, "same_application", 0.98)
    date_a, date_b = fp_a["date"], fp_b["date"]
    if date_a and date_b and abs((date_a - date_b).days) > 90:
        return None
    # A different requisition on one employer's ATS is not a cross-post.
    for host_a, id_a in left.get("requisitions", []):
        for host_b, id_b in right.get("requisitions", []):
            if host_a == host_b and id_a and id_b and id_a != id_b:
                return None
            if same_employer and host_a == host_b and id_a and id_a == id_b:
                return Match(True, "same_requisition", 0.99)
    if (min(len(texts_a), len(texts_b)) >= 30 and left.get("kinds") == right.get("kinds") == {"facebook"}
            and title_score >= 0.8 and word >= 0.70 and phrase >= 0.55
            and _contacts(left.get("description") or "") & _contacts(right.get("description") or "")):
        return Match(True, "facebook_content", round((word + phrase) / 2, 3))
    if min(len(texts_a), len(texts_b)) >= 35:
        if same_employer and title_agreement >= 0.70 and word >= 0.87 and phrase >= 0.72:
            return Match(True, "cross_source_content", round((word + phrase) / 2, 3))
        # Unknown Facebook employers need near-identical prose, not just contacts.
        if (a in unknown or b in unknown) and title_agreement >= 0.70 and word >= 0.95 and phrase >= 0.90:
            return Match(True, "cross_source_content", round((word + phrase) / 2, 3))
    if same_employer and ((word >= 0.48 and title_score >= 0.75) or fp_a["title"] == fp_b["title"]):
        return Match(False, "Similar employer, role, and requirements; confirmation needed", round(max(word, phrase), 3))
    return None


def vacancy_records(conn: sqlite3.Connection, incoming: dict | None = None) -> list[dict]:
    query, params = "SELECT * FROM vacancies", ()
    if incoming and company_key(incoming.get("company")) not in {"", "facebookpost", "unknownemployer"}:
        keys = employer_keys(conn)
        employer = keys.get(company_key(incoming["company"]))
        ids = [row["id"] for row in conn.execute("SELECT id,name FROM employers")
               if keys.get(company_key(row["name"])) == employer and employer]
        query += f" WHERE employer_id IN ({','.join('?' for _ in ids) or 'NULL'}) OR lower(company)=lower(?) OR company IN ('Facebook post','Unknown employer')"
        params = (*ids, incoming["company"])
    records = {row["id"]: {**dict(row), "urls": [], "requisitions": [], "protected": False, "kinds": set()}
               for row in conn.execute(query, params)}
    preferred = set()
    for row in conn.execute("SELECT vo.vacancy_id,vo.merge_reason,o.url,o.external_id,o.payload,s.kind FROM vacancy_observations vo JOIN observations o ON o.id=vo.observation_id JOIN sources s ON s.id=o.source_id ORDER BY CASE s.kind WHEN 'career' THEN 0 WHEN 'linkedin' THEN 1 ELSE 2 END,o.last_seen_at DESC,o.id"):
        record = records.get(row["vacancy_id"])
        if record is None:
            continue
        if record["id"] not in preferred:
            payload = json.loads(row["payload"])
            for field in ("title", "description", "location", "work_mode", "apply_url", "published_at"):
                if payload.get(field):
                    record[field] = payload[field]
            preferred.add(record["id"])
        record["urls"].append(row["url"])
        record["kinds"].add(row["kind"])
        record["protected"] |= row["merge_reason"] == "user_split"
        if row["kind"] == "career" and row["external_id"]:
            record["requisitions"].append(career_requisition(row["url"], row["external_id"]))
        elif row["kind"] == "career" and (urlsplit(row["url"]).hostname or "").endswith(".talent.vn"):
            identifier = re.search(r"/job/[^/?]+-(\d+)/?$", row["url"])
            if identifier:
                record["requisitions"].append((urlsplit(row["url"]).hostname, identifier[1]))
    for record in records.values():
        record["_company_key"] = company_key(record["company"])
    return list(records.values())


def _distinct(conn: sqlite3.Connection, left: str, right: str) -> bool:
    pair = sorted((left, right))
    return bool(conn.execute("SELECT 1 FROM duplicate_reviews WHERE left_id=? AND right_id=? AND status='distinct'", pair).fetchone())


def remember_match(conn: sqlite3.Connection, left: str, right: str, match: Match) -> None:
    if left == right:
        return
    pair = sorted((left, right))
    timestamp = now()
    conn.execute("INSERT INTO duplicate_reviews VALUES(?,?,'possible',?,?,?,?) ON CONFLICT(left_id,right_id) DO UPDATE SET reason=CASE WHEN duplicate_reviews.reason LIKE 'Ambiguous match%' THEN duplicate_reviews.reason ELSE excluded.reason END,confidence=excluded.confidence,updated_at=excluded.updated_at WHERE duplicate_reviews.status!='distinct'",
                 (*pair, match.reason, match.confidence, timestamp, timestamp))


def find_match(conn: sqlite3.Connection, incoming: dict, records: list[dict] | None = None) -> tuple[str, Match] | None:
    keys = employer_keys(conn)
    matches = []
    records = records if records is not None else vacancy_records(conn, incoming)
    by_id = {r["id"]: r for r in records}
    for candidate in records:
        if _distinct(conn, incoming["id"], candidate["id"]):
            continue
        pair = sorted((incoming["id"], candidate["id"]))
        if conn.execute("SELECT 1 FROM duplicate_reviews WHERE left_id=? AND right_id=? AND reason LIKE 'Ambiguous match%'", pair).fetchone():
            continue
        match = assess(incoming, candidate, keys)
        if match and match.automatic:
            matches.append((candidate["id"], match))
    # Multiple candidates are safe only when they also agree with each other.
    for index, (left, _) in enumerate(matches):
        for right, _ in matches[index + 1:]:
            evidence = assess(by_id[left], by_id[right], keys)
            if not evidence or not evidence.automatic or _distinct(conn, left, right):
                return None
    return max(matches, key=lambda item: item[1].confidence) if matches else None


def reconcile_vacancy(conn: sqlite3.Connection, vacancy_id: str, profile: dict, model: str) -> str:
    """A rescan can supply the missing evidence for a previously separate job."""
    stored = conn.execute("SELECT company FROM vacancies WHERE id=?", (vacancy_id,)).fetchone()
    records = vacancy_records(conn, dict(stored))
    incoming = next((r for r in records if r["id"] == vacancy_id), None)
    match = find_match(conn, incoming, records) if incoming else None
    if not match:
        return vacancy_id
    other_id, evidence = match
    conn.execute("SAVEPOINT refreshed_duplicate")
    try:
        identifier = merge_vacancies(conn, vacancy_id, other_id, evidence.reason, profile, model)
    except ValueError as error:
        conn.execute("ROLLBACK TO refreshed_duplicate")
        conn.execute("RELEASE refreshed_duplicate")
        remember_match(conn, vacancy_id, other_id, Match(False, str(error), evidence.confidence))
        return vacancy_id
    conn.execute("RELEASE refreshed_duplicate")
    return identifier


def refresh_vacancy(conn: sqlite3.Connection, vacancy_id: str, profile: dict, matching_model: str) -> None:
    from .ingest import normalize_url
    from .location_mode import split_location_work_mode
    old = dict(conn.execute("SELECT * FROM vacancies WHERE id=?", (vacancy_id,)).fetchone())
    sightings = conn.execute("SELECT o.*,s.kind FROM vacancy_observations vo JOIN observations o ON o.id=vo.observation_id JOIN sources s ON s.id=o.source_id WHERE vo.vacancy_id=? ORDER BY CASE s.kind WHEN 'career' THEN 0 WHEN 'linkedin' THEN 1 ELSE 2 END,o.last_seen_at DESC,o.id", (vacancy_id,)).fetchall()
    if not sightings:
        return
    payloads = [json.loads(row["payload"]) for row in sightings]
    primary = payloads[0]
    fields = {key: primary.get(key) or next((p.get(key) for p in payloads if p.get(key)), old.get(key))
              for key in ("company", "title", "description", "location", "work_mode", "apply_url", "published_at")}
    fields["location"], fields["work_mode"] = split_location_work_mode(fields["location"], fields["work_mode"])
    if fields["apply_url"]:
        fields["apply_url"] = normalize_url(fields["apply_url"])
    first_seen = min([old["first_seen_at"], *[r["first_seen_at"] for r in sightings]])
    last_seen = max([old["last_seen_at"], *[r["last_seen_at"] for r in sightings]])
    changed = any((old.get(key) or "") != (fields.get(key) or "") for key in
                  ("company", "title", "description", "location", "work_mode", "apply_url", "published_at"))
    conn.execute("UPDATE vacancies SET company=?,title=?,description=?,location=?,work_mode=?,apply_url=?,published_at=?,first_seen_at=?,last_seen_at=?,updated_at=? WHERE id=?",
                 (*[fields[k] for k in ("company", "title", "description", "location", "work_mode", "apply_url", "published_at")], first_seen, last_seen, now(), vacancy_id))
    if changed and old["analysis_status"] not in {"dismissed", "deferred"}:
        conn.execute("DELETE FROM user_job_scores WHERE vacancy_id=?", (vacancy_id,))
        conn.execute("DELETE FROM job_facts WHERE vacancy_id=?", (vacancy_id,))
        score, detail = score_job({**fields, "first_seen_at": first_seen}, profile)
        conn.execute("UPDATE vacancies SET score=?,score_detail=?,analysis_status=?,analysis_error=NULL WHERE id=?",
                     (score, json.dumps(detail, ensure_ascii=False), "pending" if matching_model else "not_configured", vacancy_id))
    conn.execute("UPDATE vacancy_fts SET title=?,company=?,description=? WHERE vacancy_id=?", (fields["title"], fields["company"], fields["description"], vacancy_id))


def merge_vacancies(conn: sqlite3.Connection, left_id: str, right_id: str, reason: str, profile: dict, matching_model: str) -> str:
    if left_id == right_id:
        return left_id
    rows = [dict(r) for r in conn.execute("SELECT * FROM vacancies WHERE id IN (?,?)", (left_id, right_id))]
    if len(rows) != 2:
        raise KeyError("Job not found")
    # Keep the identifier with the most user history; all histories still move.
    def priority(row):
        history = conn.execute("SELECT COUNT(*) FROM application_drafts WHERE vacancy_id=?", (row["id"],)).fetchone()[0]
        return (-history, row["first_seen_at"], row["id"])
    survivor, removed = sorted(rows, key=priority)
    keep, drop = survivor["id"], removed["id"]
    attempts = [dict(r) for r in conn.execute("SELECT * FROM auto_application_attempts WHERE vacancy_id IN (?,?)", (keep, drop))]
    if any(r["status"] in {"preparing", "regenerating", "running", "sending"} for r in attempts) or any(r["analysis_status"] == "running" for r in rows):
        raise ValueError("Wait for active application preparation to finish before combining these jobs")
    contacts = conn.execute("SELECT phone,COUNT(*) FROM zalo_inquiry_attempts WHERE vacancy_id IN (?,?) AND status IN ('sending','sent_confirmed','send_uncertain') GROUP BY phone HAVING COUNT(*)>1", (keep, drop)).fetchall()
    if contacts:
        raise ValueError("These jobs have separate active Zalo contact histories; keep them separate")
    if survivor["decision_state"] != "undecided" and removed["decision_state"] != "undecided" and survivor["decision_state"] != removed["decision_state"]:
        raise ValueError("These jobs have different decisions; review the decisions before combining")
    if survivor["recruiting_outcome"] != "none" and removed["recruiting_outcome"] != "none" and survivor["recruiting_outcome"] != removed["recruiting_outcome"]:
        raise ValueError("These jobs have different recruiting outcomes; keep them separate")
    snapshot = {"survivor": survivor, "removed": removed, "references": {}}
    for table in ("application_drafts", "submissions", "auto_application_attempts", "notification_attempts", "notification_events", "feedback", "zalo_inquiry_drafts", "zalo_inquiry_attempts", "vacancy_observations"):
        snapshot["references"][table] = [dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE vacancy_id IN (?,?)", (keep, drop))]
    snapshot["reviews"] = [dict(r) for r in conn.execute("SELECT * FROM duplicate_reviews WHERE left_id IN (?,?) OR right_id IN (?,?)", (keep, drop, keep, drop))]
    for table in ("application_drafts", "submissions", "notification_events", "feedback", "zalo_inquiry_drafts", "zalo_inquiry_attempts"):
        conn.execute(f"UPDATE {table} SET vacancy_id=? WHERE vacancy_id=?", (keep, drop))
    for row in conn.execute("SELECT * FROM notification_attempts WHERE vacancy_id=?", (drop,)).fetchall():
        existing = conn.execute("SELECT * FROM notification_attempts WHERE vacancy_id=? AND channel=?", (keep, row["channel"])).fetchone()
        if existing:
            winner = max((dict(existing), dict(row)), key=lambda r: (r["status"] == "sent", r["attempts"]))
            conn.execute("UPDATE notification_attempts SET status=?,attempts=?,last_error=?,last_attempt_at=?,sent_at=? WHERE vacancy_id=? AND channel=?", (*[winner[k] for k in ("status", "attempts", "last_error", "last_attempt_at", "sent_at")], keep, row["channel"]))
            conn.execute("DELETE FROM notification_attempts WHERE vacancy_id=? AND channel=?", (drop, row["channel"]))
        else:
            conn.execute("UPDATE notification_attempts SET vacancy_id=? WHERE vacancy_id=? AND channel=?", (keep, drop, row["channel"]))
    if attempts:
        winner = max(attempts, key=lambda r: (r["status"] == "sent", bool(r["draft_id"]), r["status"] not in {"skipped", "queued"}, r["updated_at"]))
        conn.execute("DELETE FROM auto_application_attempts WHERE vacancy_id IN (?,?)", (keep, drop))
        winner["vacancy_id"] = keep
        conn.execute(f"INSERT INTO auto_application_attempts({','.join(winner)}) VALUES({','.join('?' for _ in winner)})", tuple(winner.values()))
    conn.execute("UPDATE vacancy_observations SET vacancy_id=?,merge_reason=CASE WHEN merge_reason='user_split' THEN merge_reason ELSE ? END WHERE vacancy_id=?", (keep, reason, drop))
    conn.execute("DELETE FROM duplicate_reviews WHERE left_id IN (?,?) OR right_id IN (?,?)", (keep, drop, keep, drop))
    for review in snapshot["reviews"]:
        pair = sorted(keep if i == drop else i for i in (review["left_id"], review["right_id"]))
        if pair[0] != pair[1]:
            conn.execute("INSERT INTO duplicate_reviews VALUES(?,?,?,?,?,?,?) ON CONFLICT(left_id,right_id) DO UPDATE SET status=CASE WHEN excluded.status='distinct' THEN 'distinct' ELSE duplicate_reviews.status END", (*pair, *[review[k] for k in ("status", "reason", "confidence", "created_at", "updated_at")]))
    conn.execute("UPDATE vacancy_redirects SET vacancy_id=? WHERE vacancy_id=?", (keep, drop))
    conn.execute("INSERT INTO vacancy_redirects VALUES(?,?)", (drop, keep))
    state = removed if survivor["decision_state"] == "undecided" and removed["decision_state"] != "undecided" else survivor
    outcome = survivor["recruiting_outcome"] if survivor["recruiting_outcome"] != "none" else removed["recruiting_outcome"]
    conn.execute("UPDATE vacancies SET decision_state=?,state=?,snoozed_until=?,recruiting_outcome=?,seen_at=COALESCE(seen_at,?),manual_applied_at=COALESCE(manual_applied_at,?),manual_applied_source=COALESCE(manual_applied_source,?),first_seen_at=min(first_seen_at,?),last_seen_at=max(last_seen_at,?) WHERE id=?",
                 (state["decision_state"], state["state"], state["snoozed_until"], outcome, removed["seen_at"], removed["manual_applied_at"], removed["manual_applied_source"], removed["first_seen_at"], removed["last_seen_at"], keep))
    if removed["analysis_status"] == "dismissed":
        conn.execute("UPDATE vacancies SET analysis_status='dismissed',score=NULL,score_detail=NULL WHERE id=?", (keep,))
    conn.execute("DELETE FROM vacancy_fts WHERE vacancy_id=?", (drop,))
    conn.execute("DELETE FROM vacancies WHERE id=?", (drop,))
    conn.execute("INSERT INTO vacancy_merge_log VALUES(?,?,?,?,?,?)", (new_id(), keep, drop, reason, json.dumps(snapshot, ensure_ascii=False), now()))
    refresh_vacancy(conn, keep, profile, matching_model)
    return keep


def deduplicate_pool(db: Database, *, apply: bool = False) -> dict:
    profile, model = db.get_setting("profile", {}), db.get_setting("matching_model", "")
    report = {"merged": [], "possible": [], "blocked": []}
    with db.connection() as conn:
        records = vacancy_records(conn)
        keys = employer_keys(conn)
        redirects = {}
        members = {row["id"]: [row] for row in records}
        for index, left in enumerate(records):
            for right in records[index + 1:]:
                a, b = redirects.get(left["id"], left["id"]), redirects.get(right["id"], right["id"])
                if a == b:
                    continue
                match = assess(left, right, keys)
                if not match or _distinct(conn, a, b):
                    continue
                pair = sorted((a, b))
                review = conn.execute("SELECT reason FROM duplicate_reviews WHERE left_id=? AND right_id=? AND status='possible'", pair).fetchone()
                if review and review["reason"].startswith("Ambiguous match"):
                    match = Match(False, review["reason"], match.confidence)
                item = {"left_id": a, "right_id": b, "company": left["company"], "title": left["title"], "reason": match.reason, "confidence": match.confidence}
                if match.automatic:
                    compatible = [assess(x, y, keys) for x in members[a] for y in members[b]]
                    if not all(m and m.automatic for m in compatible):
                        item["reason"] = "Ambiguous match to a combined job; confirmation needed"
                        if apply:
                            remember_match(conn, a, b, Match(False, item["reason"], match.confidence))
                        report["possible"].append(item)
                        continue
                    if apply:
                        # Each pair is atomic even when an active history blocks a merge.
                        conn.execute("SAVEPOINT duplicate_pair")
                        try:
                            survivor = merge_vacancies(conn, a, b, match.reason, profile, model)
                        except ValueError as error:
                            conn.execute("ROLLBACK TO duplicate_pair")
                            conn.execute("RELEASE duplicate_pair")
                            item["blocker"] = str(error)
                            remember_match(conn, a, b, Match(False, str(error), match.confidence))
                            report["blocked"].append(item)
                            continue
                        conn.execute("RELEASE duplicate_pair")
                        dropped = b if survivor == a else a
                        redirects = {key: survivor if value == dropped else value for key, value in redirects.items()}
                        redirects[dropped] = survivor
                        item["survivor_id"] = survivor
                    else:
                        survivor = a
                        dropped = b
                        redirects = {key: survivor if value == dropped else value for key, value in redirects.items()}
                        redirects[dropped] = survivor
                    members[survivor] = members[a] + members[b]
                    report["merged"].append(item)
                else:
                    if apply:
                        remember_match(conn, a, b, match)
                    report["possible"].append(item)
        report["before"] = len(records)
        report["after"] = conn.execute("SELECT COUNT(*) FROM vacancies").fetchone()[0] if apply else len(records) - len(report["merged"])
        report["applied"] = apply
    return report
