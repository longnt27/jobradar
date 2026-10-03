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


def browser(employer: str, url: str, path: str, **options) -> CareerFeed:
    return CareerFeed(employer, url, "browser_board", {"link_path": path, **options})


TALENT_JOB = r"^/job/[^/?#]+$"

CAREER_FEEDS: tuple[CareerFeed, ...] = (
    CareerFeed("VinAI", "https://www.vinai.io/careers/", "legacy"),
    CareerFeed("VinDynamics", "https://vindynamics.net/career", "vindynamics"),
    CareerFeed("VinRobotics", "https://vinrobotics.net/career", "vinrobotics"),
    html("VinMotion", "https://vinmotion.net/vi/career", r"^/vi/career/[^/?#]+$", max_results=100, description_selector="main"),
    html("VinUni", "https://vinuni.talent.vn/jobs", TALENT_JOB, max_results=100, description_selector=".content-article, article"),
    CareerFeed("Viettel Group", "https://jobs.viettel.vn/search/?q=AI", "successfactors", {"queries": ["AI", "kỹ sư", "Data Scientist", "Viettel High Tech", "Viettel Cyber Security"], "max_pages": 2}),
    html("FPT Education", "https://career.fpt.edu.vn/Job/Search", r"^/Job/Detail/\d+$", description_selector=".job_description_content"),
    browser("FPT Telecom", "https://fptjobs.com/tuyen-dung", r"^/[a-z0-9-]+-\d+$", listing_selector="a.link-overlay[href]", listing_ready_selector="a.link-overlay[href*='-'][title]", description_selector="main"),
    CareerFeed("Techcombank", "https://www.techcombankjobs.com/go/Data-%26-Analytics/549144/", "successfactors", {
        "boards": ["https://www.techcombankjobs.com/go/Data-%26-Analytics/549144/", "https://www.techcombankjobs.com/go/Technology/548944/"], "max_pages": 4,
    }),
    CareerFeed("Vietcombank", "https://tuyendung.vietcombank.com.vn/go/IT/572944/", "successfactors", {
        "boards": ["https://tuyendung.vietcombank.com.vn/go/IT/572944/", "https://tuyendung.vietcombank.com.vn/go/Tin-h%E1%BB%8Dc-CN_Batch/570844/"], "max_pages": 3,
    }),
    CareerFeed("MB Bank", "https://careers.mbbank.com.vn/list-of-posts", "mbbank", {
        "queries": ["AI Engineer", "Data", "DevOps", "Developer", "Software", "Kỹ sư", "Công nghệ thông tin"],
    }),
    CareerFeed("BIDV", "https://tuyendung.bidv.com.vn/danh-sach-viec-lam-moi.html", "bidv"),
    CareerFeed("VietinBank", "https://tuyendung.vietinbank.vn/tuyendung/tuyen-dung", "vietinbank"),
    browser("TPBank", "https://tuyendung.tpb.vn/vi/jobs", r"^/vi/jobs/[A-Za-z0-9]+$", max_pages=11, description_selector="main"),
    browser("LPBank", "https://tuyendung.lpbank.com.vn/vi/jobs", r"^/vi/jobs/[A-Za-z0-9]+$", max_pages=14, description_selector="main"),
    html("MSB", "https://jobs.msb.com.vn/latest-jobs", r"^/jobs/[^/?#]+-\d+$", description_selector=".job_description"),
    html("SeABank", "https://tuyendung.seabank.com.vn/jobs?page=1", r"^/jobs/[^/?#]+\.\d+$", max_pages=10, description_selector="section"),
    html("BaoViet Bank", "https://www.baovietbank.vn/tuyen-dung/", r"^/tuyen-dung/Home/Detail/[^/?#]+$", keep_query=True, description_selector=".bvb-info-recruitment"),
    html("ABBank", "https://careers.abbank.vn/jobs", r"^/job/[^/?#]+-\d+$", max_pages=11, max_results=150, description_selector="article"),
    html("PGBank", "https://tuyendung.pgbank.com.vn/", r"^/viec-lam/[^/?#]+\.[a-f0-9]+\.html$", description_selector=".job-post-description"),
    browser("VietABank", "https://tuyendung.vietabank.com.vn/vi/jobs", r"^/vi/jobs/[A-Za-z0-9]+$", max_pages=12, detail_ready_min_chars=200, description_selector="main"),
    browser("MBV", "https://www.mbv.com.vn/tuyen-dung/co-hoi-nghe-nghiep", r"^/viec-lam/[^/?#]+$", listing_selector="a[href*='/viec-lam/']", description_selector="main"),
    html("VPBank", "https://vpbank.talent.vn/jobs?dept=3326", TALENT_JOB, max_results=100, max_pages=5, description_selector=".content-article, .article, article"),
    html("GPBank", "https://gpbank.talent.vn/jobs?dept=1538", TALENT_JOB, max_results=100, max_pages=4, description_selector=".content-article, .article, article"),
    html("CMC Global", "https://cmcglobal.com.vn/career/", r"^/career/[^/?#]+/?$", max_pages=10, pagination="wordpress", max_results=120, description_selector="main"),
    html("CMC TS", "https://careers.cmcts.com.vn/", TALENT_JOB, max_results=100, description_selector=".content-article, article"),
    html("CMC Telecom", "https://cmctelecom.vn/danh-sach-tuyen-dung/", r"^/recruit/[^/?#]+/?$", description_selector=".recruitment-detail"),
    html("NTQ Solution", "https://career.ntq.com.vn/careers", r"^/career/[^/?#]+/?$", description_selector=".section-career-detail__job-content__description"),
    html("VNPAY", "https://tuyendung.vnpay.vn/", r"^/tuyen-dung/[^/?#]+\.html$"),
    html("Base.vn", "https://baseinc.talent.vn/alljobs?dept=9", TALENT_JOB, description_selector=".content-article, article"),
    html("VNDIRECT", "https://vndirect.talent.vn/jobs", TALENT_JOB, max_results=100, max_pages=4, description_selector=".content-article, .article, article"),
    html("TeenCare", "https://teencare.talent.vn/", TALENT_JOB, description_selector=".content-article, article"),
    html("Golden Gate", "https://ggg.talent.vn/", TALENT_JOB, description_selector=".content-article, article"),
    html("Eastgate Software", "https://eastgatesoftware.talent.vn/jobs", TALENT_JOB, description_selector=".content-article, article"),
    CareerFeed("SmartOSC", "https://careers.smartrecruiters.com/SmartOSC", "smartrecruiters", {"company_slug": "SmartOSC"}),
    html("KiotViet", "https://about.kiotviet.vn/cong-viec/", r"^/jobs/[^/?#]+/?$", description_selector=".box-detail-content"),
    html("MISA", "https://www.misa.vn/tuyen-dung/", r"^/tuyen-dung/\d+/[^/?#]+/?$", max_results=120, description_selector=".recruitment-detail"),
    html("Sapo", "https://tuyendung.sapo.vn/", r"^/co-hoi-viec-lam/[^/?#]+-a\d+\.html$", description_selector=".col-lg-8.col-12"),
    browser("Cốc Cốc", "https://careers.coccoc.com/jobs", r"^/jobs/[^/?#]+$", description_selector="main"),
    html("VNPT AI", "https://tuyendung.vnpt.vn/viec-lam/don-vi-cong-ty-vnpt-ai-d3678304.html", r"^/tim-viec-lam/[^/?#]+-jid\d+\.html$", max_pages=12, pagination="vnpt", description_selector=".detail-left"),
    html("VNPT IT", "https://tuyendung.vnpt.vn/viec-lam/don-vi-cong-ty-cong-nghe-thong-tin-vnpt-d9621.html", r"^/tim-viec-lam/[^/?#]+-jid\d+\.html$", max_pages=12, pagination="vnpt", description_selector=".detail-left"),
    html("VNPT Net", "https://tuyendung.vnpt.vn/viec-lam/don-vi-tong-cong-ty-ha-tang-mang-d8381.html", r"^/tim-viec-lam/[^/?#]+-jid\d+\.html$", max_pages=12, pagination="vnpt", description_selector=".detail-left"),
    html("VNPT Media", "https://tuyendung.vnpt.vn/viec-lam/don-vi-tong-cong-ty-truyen-thong-d8606.html", r"^/tim-viec-lam/[^/?#]+-jid\d+\.html$", max_pages=12, pagination="vnpt", description_selector=".detail-left"),
)
