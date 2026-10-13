"""Inspect first-party career HTML structure on GitHub-hosted runners.

This debug tool issues one bounded GET per source and prints only public page
shape metadata. It does not crawl deep links or bypass 403/429/TLS failures.
"""
from __future__ import annotations
import asyncio
import json
import os
import re
import ssl
import httpx
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlsplit

SITES = {
    "co-opbank": "https://co-opbank.vn/tuyen-dung/",
    "co-opbank-www": "https://www.co-opbank.vn/tuyen-dung/",
    "vnpt": "https://www.vnpt-technology.vn/vi/recruit",
    "anduin": "https://anduin.hire.trakstar.com/jobs/fk0zl8s/",
    "cmc": "https://cmcu.edu.vn/tin-tuyen-dung/",
    "mirae": "https://recruitment.mafc.vn/job-search/all-jobs/en",
    "gear": "https://gear-inc-2.careers-page.com/",
    "anduin-board": "https://anduin.hire.trakstar.com/",
    "cmc-www": "https://www.cmcu.edu.vn/tin-tuyen-dung/",
    "mirae-www": "https://www.recruitment.mafc.vn/job-search/all-jobs/en",
    "baoviet-www": "https://www.baoviet.com.vn/vi/tuyen-dung",
    "baoviet-bare": "https://baoviet.com.vn/vi/tuyen-dung",
    "ampere": "https://careers.amperecomputing.com/search/jobs/in/sg-ho-chi-minh-sai-gon",
    "sap": "https://jobs.sap.com/go/Vietnam/9513801/",
    "fpt": "https://career.fpt-software.com/jobs-search/",
    "agoda": "https://careersatagoda.com/vacancies/",
    "qualcomm": "https://careers.qualcomm.com/api/pcsx/search?domain=qualcomm.com&location=Vietnam&start=0",
    "qualcomm-blank-query": "https://careers.qualcomm.com/api/pcsx/search?domain=qualcomm.com&query=&location=Vietnam&start=0",
}
async def inspect(name, url, verify=None):
    async with httpx.AsyncClient(follow_redirects=True, timeout=18, verify=verify if verify else True,
                                 headers={"User-Agent": "JobRadar/0.1 (public recruitment feed health check)"}) as c:
        try:
            r = await c.get(url)
            s = BeautifulSoup(r.text, "html.parser")
            anchors = [(urljoin(str(r.url), a.get("href", "")), a.get_text(" ", strip=True)[:70])
                       for a in s.select("a[href]")]
            wanted = [(u, t) for u, t in anchors if re.search("recruit|tuyen|job|career|vi[eệ]c.l[aà]m", u, re.I)]
            text = s.get_text(" ", strip=True)
            info = {
                "name": name, "status": r.status_code, "final_host": urlsplit(str(r.url)).hostname,
                "path": urlsplit(str(r.url)).path,
                "page_title": s.title.get_text(" ", strip=True)[:130] if s.title else None,
                "h1": [n.get_text(" ", strip=True)[:100] for n in s.select("h1")[:3]],
                "anchor_count": len(anchors), "candidate_anchors": wanted[:12],
                "body_chars": len(text), "recruit_detail_in_source": len(re.findall("recruit_detail", r.text, re.I)),
                "contains_no_jobs_marker": bool(re.search("không có vị trí|chưa có vị trí|no vacancies|chưa có bài", text, re.I)),
                "server": r.headers.get("server"), "retry_after": r.headers.get("retry-after"),
            }
            if name.startswith("qualcomm") and r.status_code == 200:
                try:
                    payload = r.json()
                    body = payload.get("data") if isinstance(payload, dict) else {}
                    records = body.get("positions", []) if isinstance(body, dict) else []
                    info["json_top_keys"] = list(payload)[:20] if isinstance(payload, dict) else []
                    info["json_data_keys"] = list(body)[:20] if isinstance(body, dict) else []
                    info["positions"] = len(records) if isinstance(records, list) else "not a list"
                    info["total"] = body.get("count") if isinstance(body, dict) else None
                    info["sample_location"] = (records[0].get("locations") or records[0].get("standardizedLocations")) if isinstance(records, list) and records and isinstance(records[0],dict) else None
                except ValueError:
                    info["json_parse_error"] = "Not JSON"
            print(json.dumps(info, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps({"name": name, "failure": type(exc).__name__, "reason": str(exc)[:300],
                              "trust": "os-ca" if verify else "certifi"}, ensure_ascii=False), flush=True)

async def main():
    selected = os.environ.get("DIAG_SITE", "")
    for name, url in SITES.items():
        if selected and not name.startswith(selected):
            continue
        await inspect(name, url)
        if name in {"cmc", "mirae"}:
            await inspect(name + "-system-ca", url, ssl.create_default_context())
if __name__ == "__main__":
    asyncio.run(main())
