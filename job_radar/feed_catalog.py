"""Verified employer career feeds. A directory entry alone is never a scan source.

Each feed names a collector strategy and the site-specific listing shape. The
URLs below are employer-operated sites or their linked recruiting platforms.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CareerFeed:
    employer: str
    url: str
    adapter: str
    options: dict = field(default_factory=dict)


def html(employer: str, url: str, path: str, **options) -> CareerFeed:
    return CareerFeed(employer, url, "html_board", {"link_path": path, **options})


TALENT_JOB = r"^/job/[^/?#]+$"

CAREER_FEEDS: tuple[CareerFeed, ...] = (
    CareerFeed("VinAI", "https://www.vinai.io/careers/", "legacy"),
    CareerFeed("VinDynamics", "https://vindynamics.net/career", "vindynamics"),
    CareerFeed("VinRobotics", "https://vinrobotics.net/career", "vinrobotics"),
    html("VinUni", "https://vinuni.talent.vn/jobs", TALENT_JOB, max_results=100, description_selector=".content-article, article"),
    CareerFeed("Viettel Group", "https://jobs.viettel.vn/search/?q=AI", "successfactors", {"queries": ["AI", "kỹ sư", "Data Scientist"], "max_pages": 2}),
    html("FPT Education", "https://career.fpt.edu.vn/Job/Search", r"^/Job/Detail/\d+$", description_selector=".job_description_content"),
    CareerFeed("Techcombank", "https://www.techcombankjobs.com/go/Data-%26-Analytics/549144/", "successfactors", {
        "boards": ["https://www.techcombankjobs.com/go/Data-%26-Analytics/549144/", "https://www.techcombankjobs.com/go/Technology/548944/"], "max_pages": 4,
    }),
    CareerFeed("Vietcombank", "https://tuyendung.vietcombank.com.vn/go/IT/572944/", "successfactors", {
        "boards": ["https://tuyendung.vietcombank.com.vn/go/IT/572944/", "https://tuyendung.vietcombank.com.vn/go/Tin-h%E1%BB%8Dc-CN_Batch/570844/"], "max_pages": 3,
    }),
    CareerFeed("MB Bank", "https://careers.mbbank.com.vn/list-of-posts", "mbbank", {
        "queries": ["AI Engineer", "Data", "DevOps", "Developer", "Software", "Kỹ sư", "Công nghệ thông tin"],
    }),
    html("VPBank", "https://vpbank.talent.vn/jobs?dept=3326", TALENT_JOB, max_results=100, max_pages=5, description_selector=".content-article, .article, article"),
    html("GPBank", "https://gpbank.talent.vn/jobs?dept=1538", TALENT_JOB, max_results=100, max_pages=4, description_selector=".content-article, .article, article"),
    html("ACB", "https://acbjobs.talent.vn/jobs", TALENT_JOB, max_results=100, max_pages=6, description_selector=".content-article, .article, article"),
    html("MoMo", "https://momo.careers/jobs-opening", r"^/jobs/[^/?#]+$", description_selector="main"),
    html("Katalon", "https://careers.katalon.com/", r"^/jobs/\d+-[^/]+$", description_selector="main"),
    html("ELSA", "https://elsaspeak.com/en/career", r"^/jobs/\d+-[^/]+$", allowed_hosts=["elsa.teamtailor.com"], description_selector="main"),
    html("CMC Global", "https://cmcglobal.com.vn/career/", r"^/career/[^/?#]+/?$", max_pages=10, pagination="wordpress", max_results=120, description_selector="main"),
    html("CMC TS", "https://careers.cmcts.com.vn/", TALENT_JOB, max_results=100, description_selector=".content-article, article"),
    html("TMA Solutions", "https://www.tma.vn/tuyen-dung/viec-lam", r"^/tuyen-dung/chi-tiet/[^/?#]+$"),
    html("NTQ Solution", "https://career.ntq.com.vn/careers", r"^/career/[^/?#]+/?$", description_selector=".section-career-detail__job-content__description"),
    html("Axon Active", "https://www.careers.axonactive.com/", r"^/post/[^/?#]+$", description_selector="article"),
    html("VNPAY", "https://tuyendung.vnpay.vn/", r"^/tuyen-dung/[^/?#]+\.html$"),
    html("Base.vn", "https://baseinc.talent.vn/alljobs?dept=9", TALENT_JOB, description_selector=".content-article, article"),
    html("VNDIRECT", "https://vndirect.talent.vn/jobs", TALENT_JOB, max_results=100, max_pages=4, description_selector=".content-article, .article, article"),
    html("TeenCare", "https://teencare.talent.vn/", TALENT_JOB, description_selector=".content-article, article"),
    html("Golden Gate", "https://ggg.talent.vn/", TALENT_JOB, description_selector=".content-article, article"),
    html("Cloud Ace", "https://cloudace.talent.vn/", TALENT_JOB, description_selector=".content-article, article"),
    html("Eastgate Software", "https://eastgatesoftware.talent.vn/jobs", TALENT_JOB, description_selector=".content-article, article"),
    html("Innovature BPO", "https://innovaturebpo.talent.vn/jobs", TALENT_JOB, description_selector=".content-article, article"),
    html("TARA JSC", "https://tara.talent.vn/jobs", TALENT_JOB, description_selector=".content-article, article"),
    CareerFeed("KMS Technology", "https://careers.smartrecruiters.com/kmstechnology1", "smartrecruiters", {"company_slug": "kmstechnology1"}),
    CareerFeed("SmartOSC", "https://careers.smartrecruiters.com/SmartOSC", "smartrecruiters", {"company_slug": "SmartOSC"}),
)
