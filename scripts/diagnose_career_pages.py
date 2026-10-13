"""Inspect first-party career HTML structure on GitHub-hosted runners.

This debug tool issues one bounded GET per source and prints only public page
shape metadata. It does not crawl deep links or bypass 403/429/TLS failures.
"""
from __future__ import annotations
import asyncio
import json
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
            print(json.dumps(info, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps({"name": name, "failure": type(exc).__name__, "reason": str(exc)[:300],
                              "trust": "os-ca" if verify else "certifi"}, ensure_ascii=False), flush=True)

async def main():
    for name, url in SITES.items():
        await inspect(name, url)
        if name in {"cmc", "mirae"}:
            await inspect(name + "-system-ca", url, ssl.create_default_context())
if __name__ == "__main__":
    asyncio.run(main())
