"""Verified employer career feeds. A directory entry alone is never a scan source.

Each feed names a collector strategy and the site-specific listing shape. The
URLs below are employer-operated sites or their linked recruiting platforms.
"""

from dataclasses import dataclass, field


CAREER_SCAN_INTERVAL_MINUTES = 1440


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
    CareerFeed("VPBank", "https://tuyendung.vpbank.com.vn/", "vpbank"),
    html("GPBank", "https://gpbank.talent.vn/jobs?dept=1538", TALENT_JOB, max_results=100, max_pages=4, description_selector=".content-article, .article, article"),
    html("PVcomBank", "https://tuyendung.pvcombank.com.vn/viec-lam/tat-ca-viec-lam/vi", r"^/viec-lam/[^/?#]+\.[a-f0-9]+\.html$", description_selector=".section-detail, .content_fck, .job-post-description"),
    html("SHB", "https://tuyendung.shb.com.vn/tim-viec-lam/tat-ca-viec-lam/vi", r"^/viec-lam/[^/?#]+\.[a-f0-9]+\.html$", description_selector=".job-post-description, .content_fck"),
    browser("NCB", "https://tuyendung.ncb-bank.vn/vi/jobs", r"^/vi/+jobs/[A-Za-z0-9]+$", listing_selector="a[href*='/jobs/']", description_selector="main"),
    html("KienlongBank", "https://tuyendung.kienlongbank.com/?s=&post_type=tuyen-dung", r"^/tuyen-dung/[^/?#]+/?$", pagination="wordpress", max_pages=4, description_selector=".post-description, .page-content-tuyen-dung"),
    CareerFeed("One Mount", "https://careers.onemount.com/job-list", "onemount"),
    html("CMC Global", "https://cmcglobal.com.vn/career/", r"^/career/[^/?#]+/?$",
         max_pages=10, pagination="wordpress", max_results=40, description_selector="main",
         listing_card_class="__careers-post-wrapper", listing_title_selector="h3",
         title_include=r"\b(?:AI|ML|LLM|NLP)\b|machine learning|computer vision|deep learning|data scien|generative"),
    html("CMC TS", "https://careers.cmcts.com.vn/", TALENT_JOB, max_results=100, description_selector=".content-article, article"),
    html("CMC Telecom", "https://cmctelecom.vn/danh-sach-tuyen-dung/", r"^/recruit/[^/?#]+/?$", description_selector=".recruitment-detail"),
    html("NTQ Solution", "https://career.ntq.com.vn/careers", r"^/career/[^/?#]+/?$", description_selector=".section-career-detail__job-content__description"),
    html("VNPAY", "https://tuyendung.vnpay.vn/", r"^/tuyen-dung/[^/?#]+\.html$"),
    html("Base.vn", "https://baseinc.talent.vn/alljobs?dept=9", TALENT_JOB, description_selector=".content-article, article"),
    html("VNDIRECT", "https://vndirect.talent.vn/jobs", TALENT_JOB, max_results=100, max_pages=4, description_selector=".content-article, .article, article"),
    html("TeenCare", "https://teencare.talent.vn/", TALENT_JOB, description_selector=".content-article, article"),
    html("Golden Gate", "https://ggg.talent.vn/", TALENT_JOB, description_selector=".content-article, article"),
    html("Eastgate Software", "https://eastgatesoftware.talent.vn/jobs", TALENT_JOB, description_selector=".content-article, article"),
    html("SmartOSC", "https://careers.smartosc.com/jobs", r"^/job/[^/?#]+/?$", pagination="wordpress", max_pages=5, description_selector="main, .job-overview"),
    html("KiotViet", "https://about.kiotviet.vn/cong-viec/", r"^/jobs/[^/?#]+/?$", description_selector=".box-detail-content"),
    html("MISA", "https://www.misa.vn/tuyen-dung/", r"^/tuyen-dung/\d+/[^/?#]+/?$", max_results=120, description_selector=".recruitment-detail"),
    html("Sapo", "https://tuyendung.sapo.vn/", r"^/co-hoi-viec-lam/[^/?#]+-a\d+\.html$", description_selector=".col-lg-8.col-12"),
    browser("Cốc Cốc", "https://careers.coccoc.com/jobs", r"^/jobs/[^/?#]+$", detail_ready_selector=".content-job, body", description_selector=".content-job, body, main"),
    html("VNPT AI", "https://tuyendung.vnpt.vn/viec-lam/don-vi-cong-ty-vnpt-ai-d3678304.html", r"^/tim-viec-lam/[^/?#]+-jid\d+\.html$", max_pages=12, pagination="vnpt", description_selector=".detail-left"),
    html("VNPT IT", "https://tuyendung.vnpt.vn/viec-lam/don-vi-cong-ty-cong-nghe-thong-tin-vnpt-d9621.html", r"^/tim-viec-lam/[^/?#]+-jid\d+\.html$", max_pages=12, pagination="vnpt", description_selector=".detail-left"),
    html("VNPT Net", "https://tuyendung.vnpt.vn/viec-lam/don-vi-tong-cong-ty-ha-tang-mang-d8381.html", r"^/tim-viec-lam/[^/?#]+-jid\d+\.html$", max_pages=12, pagination="vnpt", description_selector=".detail-left"),
    html("VNPT Media", "https://tuyendung.vnpt.vn/viec-lam/don-vi-tong-cong-ty-truyen-thong-d8606.html", r"^/tim-viec-lam/[^/?#]+-jid\d+\.html$", max_pages=12, pagination="vnpt", description_selector=".detail-left"),
    CareerFeed("Viettel High Tech", "https://jobs.viettel.vn/search/?q=Viettel+High+Tech", "successfactors", {"queries": ["Viettel High Tech"], "max_pages": 2}),
    CareerFeed("Viettel Cyber Security", "https://jobs.viettel.vn/search/?q=Viettel+Cyber+Security", "successfactors", {"queries": ["Viettel Cyber Security"], "max_pages": 2}),
    CareerFeed("Viettel Solutions", "https://jobs.viettel.vn/search/?q=Viettel+Solutions", "successfactors", {"queries": ["Viettel Solutions", "Tổng Công ty Giải pháp"], "max_pages": 2}),
    CareerFeed("Viettel Digital", "https://jobs.viettel.vn/search/?q=Viettel+Digital", "successfactors", {"queries": ["Viettel Digital", "Tổng Công ty Dịch vụ số"], "max_pages": 2}),
    CareerFeed("Viettel Telecom", "https://jobs.viettel.vn/search/?q=Viettel+Telecom", "successfactors", {"queries": ["Viettel Telecom", "Tổng Công ty Viễn thông"], "max_pages": 2}),
    CareerFeed("Viettel IDC", "https://jobs.viettel.vn/search/?q=Viettel+IDC", "successfactors", {"queries": ["Viettel IDC"], "max_pages": 2}),
    CareerFeed("Viettel AI", "https://jobs.viettel.vn/search/?q=Viettel+AI", "successfactors", {"queries": ["Viettel AI", "Không gian mạng"], "max_pages": 2}),
    CareerFeed("Viettel Software", "https://jobs.viettel.vn/search/?q=Viettel+Software", "successfactors", {"queries": ["Viettel Software", "Trung tâm Phần mềm"], "max_pages": 2}),
    CareerFeed("Viettel Research and Development", "https://jobs.viettel.vn/search/?q=Viettel+R%26D", "successfactors", {"queries": ["Viettel R&D", "Viện Hàng không Vũ trụ"], "max_pages": 2}),
    CareerFeed("NVIDIA", "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite", "workday", {
        "search_url": "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs",
        "detail_base": "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite",
        "search_text": "Vietnam",
    }),
    CareerFeed("Shopee", "https://careers.shopee.vn/jobs", "workatsea"),
    CareerFeed("SPX Express", "https://careers.shopee.vn/jobs", "workatsea", {"company_filter": "SPX"}),
    html("Vinschool", "https://tuyendung.vinschool.edu.vn/jobs", TALENT_JOB, description_selector=".content-article, article"),
    html("FPT University", "https://career.fpt.edu.vn/Job/Search", r"^/Job/Detail/\d+$", description_selector=".job_description_content"),
    html("9Pay", "https://9pay.vn/tuyen-dung", r"^/tuyen-dung/\d+-\d+-\d+$", listing_card_class="job-item", listing_title_selector=".job-item__title, [class*=title]", description_selector=".detail-job, .detail-job__body"),
    html("SSI", "https://tuyendung.ssi.com.vn/tin-tuyen-dung", r"^/tin-tuyen-dung/[a-z0-9-]+$", max_pages=3, description_selector="main"),
    html("TCBS", "https://www.tcbs.com.vn/ve-chung-toi-cate/tuyen-dung/", r"^/ve-chung-toi-cate/tuyen-dung/[a-z0-9-]+/?$", max_results=40, description_selector="main, .entry-content, article"),
    html("FPT Smart Cloud", "https://fptsmartcloud.com/co-hoi-nghe-nghiep/", r"^/job_opportunity/[a-z0-9-]+/?$", max_results=40, description_selector="main, .entry-content, article, [class*=job]"),
    html("VNCS Global", "https://vncsglobal.vn/tuyen-dung/", r"^/[a-z0-9-]+/?$",
         listing_card_class="elementor-post", listing_title_selector=".elementor-post__title",
         title_include=r"\S", description_selector=".elementor-widget-theme-post-content"),
    html("Sun Asterisk", "https://sun-asterisk.vn/recruitment-category/software-engineer/", r"^/recruitment/[a-z0-9-]+/?$",
         pagination="wordpress", max_pages=8, description_selector=".single-recruitment-content",
         location_selector=".single-recruitment-content .recruitment-item-location"),
    CareerFeed("NashTech Vietnam", "https://careers.nashtechglobal.com/jobs-finder/", "json_board", {
        "api_url": "https://careers.nashtechglobal.com/wp-json/wp/v2/job", "params": {"_embed": 1},
        "page_param": "page", "limit_param": "per_page", "title_field": "title.rendered", "url_field": "link",
        "description_sections": {"": "content.rendered"}, "published_field": "date_gmt",
        "location_taxonomy": "location", "mode_taxonomy": "workplace-type", "location_include": "Vietnam",
        "detail_selector": ".section-content", "location_selector": ".location-container",
    }),
    CareerFeed("Sotatek", "https://www.sotatek.com/careers-list/", "json_board", {
        "api_url": "https://www.sotatek.com/wp-admin/admin-ajax.php", "method": "POST",
        "params": {"action": "Filter_job", "data_form": ""}, "rows_path": "data.data_result", "total_field": "data.total_post",
        "id_field": "ID", "title_field": "title", "url_field": "post_link",
        "location_fields": ["job_child_term.name", "job_parent_term.name"], "detail_selector": ".job-content",
    }),
    CareerFeed("EPAM Vietnam", "https://careers.epam.com/en/jobs/vietnam", "json_board", {
        "api_url": "https://careers.epam.com/api/jobs/v2/search/careers-i18n",
        "params": {"facets": "country=4060741400349324343", "lang": "en", "websiteLocale": "en-us", "sortBy": "relevance;relocation=asc"},
        "rows_path": "data.jobs", "total_field": "data.total", "offset_param": "from", "limit_param": "size",
        "title_field": "name", "url_field": "seo.url", "id_field": "unique_id", "closed_fields": ["is_expired", "is_hidden"],
        "description_sections": {"Description": "description", "Responsibilities": "category.responsibilities", "Requirements": "category.requirements", "Nice to have": "category.nice_to_have"},
        "location_fields": ["city", "country"], "mode_field": "vacancy_type", "published_field": "created_at",
    }),
)
