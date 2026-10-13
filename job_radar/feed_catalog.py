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


def vingroup(employer: str, company_id: str = "") -> CareerFeed:
    return CareerFeed(employer, "https://tuyendung.vingroup.net/jobs" + (f"?companyCode={company_id}" if company_id else ""), "json_board", {
        "api_url": "https://ehiring-api.vinsmartfuture.tech/api/v1/job-posting/search-vgc",
        "params": {"countryCode": "VN", **({"OrgLv2Id": company_id} if company_id else {})},
        "rows_path": "data.items", "total_field": "data.totalRecord", "page_param": "PageIndex", "limit_param": "PageSize",
        "title_field": "title", "title_fields": ["titleCustom", "title"], "url_field": "url", "url_template": "https://tuyendung.vingroup.net/jobs/{id}",
        "detail_api_url": "https://ehiring-api.vinsmartfuture.tech/api/v1/job-posting/detail-vgc", "detail_api_params": {"id": "{id}"}, "detail_rows_path": "data",
        "description_sections": {"Responsibilities": "jobDescription", "Requirements": "jobRequirement", "Benefits": "jobBenefit"},
        "location_fields": ["locationNameVi"], "deadline_field": "deadline", "company_field": "orgLv2Id",
        "value_maps": {"orgLv2Id": {"45015271": "VinSmart Future", "45002926": "Vinhomes", "45032743": "VinCons", "45001013": "Vinpearl", "45054894": "VinFast"}},
    })


def workday(employer: str, host: str, tenant: str, board: str, facet: str) -> CareerFeed:
    base = f"https://{host}/wday/cxs/{tenant}/{board}"
    return CareerFeed(employer, f"https://{host}/{board}", "workday", {
        "search_url": base + "/jobs", "detail_base": base,
        "applied_facets": {facet: ["db69e8c8446c11de98360015c5e6daf6"]},
    })


def ainavi(employer: str, domain: str) -> CareerFeed:
    base = "https://centralize-api.ainavi.com.vn/api/recruitment/Recruitment/"
    return CareerFeed(employer, f"https://{domain}/vi/jobs", "json_board", {
        "api_url": base + "GetRecruitmentsByDomain", "params": {"Domain": domain}, "rows_path": "items", "total_field": "totalRecord",
        "page_param": "pageIndex", "limit_param": "pageSize", "title_field": "name", "url_field": "url", "url_template": f"https://{domain}/vi/jobs/{{slug}}",
        "detail_api_url": base + "GetRecruitmentBySlug", "detail_api_params": {"Domain": domain, "Slug": "{slug}"},
        "description_sections": {"Responsibilities": "description", "Requirements": "requirement", "Benefits": "benefit"},
        "published_field": "createdTime", "deadline_field": "recruitmentPublish.endTime", "required_fields": {"recruitmentPublish.isPublished": True},
        "location_fields": ["workingNewAddresses", "workingAddresses"],
    })

CAREER_FEEDS: tuple[CareerFeed, ...] = (
    CareerFeed("Gear Inc", "https://gear-inc-2.careers-page.com/", "manatal", {"max_pages": 50}),
    CareerFeed("Anduin Transactions", "https://anduin.hire.trakstar.com/", "trakstar"),
    CareerFeed("VNPT Technology", "https://www.vnpt-technology.vn/vi/recruit", "vnpt_technology"),
    CareerFeed("VNPT Group", "https://tuyendung.vnpt.vn/viec-lam/viec-lam-tap-doan-vnpt-d3240.html", "vnpt_parent", {"max_pages": 12}),
    CareerFeed("VinAI", "https://www.vinai.io/careers/", "legacy"),
    html("VinBus", "https://vinbus.vn/tuyen-dung/viec-lam", r"^/[a-z0-9-]+$",
         max_pages=10, max_results=100, description_selector="main"),
    CareerFeed("VinDynamics", "https://vindynamics.net/career", "vindynamics"),
    CareerFeed("VinRobotics", "https://vinrobotics.net/career", "vinrobotics"),
    html("VinMotion", "https://vinmotion.net/vi/career", r"^/vi/career/[^/?#]+$", max_results=100, description_selector="main"),
    html("VinUni", "https://vinuni.talent.vn/jobs", TALENT_JOB, max_results=100, description_selector=".content-article, article"),
    CareerFeed("Viettel Group", "https://jobs.viettel.vn/search/?q=AI", "successfactors", {"queries": ["AI", "kỹ sư", "Data Scientist", "Viettel High Tech", "Viettel Cyber Security"], "max_pages": 2}),
    html("FPT Education", "https://career.fpt.edu.vn/Job/Search", r"^/Job/Detail/\d+$", description_selector=".job_description_content"),
    browser("FPT Telecom", "https://fptjobs.com/tuyen-dung", r"^/[a-z0-9-]+-\d+$", listing_selector="a.link-overlay[href]", listing_ready_selector="a.link-overlay[href*='-'][title]", description_selector="main"),
    html("FPT Software", "https://career.fpt-software.com/jobs-search/", r"^/jobs/[a-z0-9][a-z0-9-]+/?$",
         pagination_param="page_job", max_pages=10, max_results=180, description_selector="main, article",
         require_links=True, strict_detail=True, external_id_from_path=True),
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
    html("CMC University", "https://cmcu.edu.vn/tin-tuyen-dung/", r"^/thong-bao-tuyen-dung-[^/?#]+/?$",
         pagination="wordpress", max_pages=3, max_results=50, description_selector=".entry-content, article",
         location_regex=r"Địa chỉ\s*:\s*(.{5,180}?)(?=\s+(?:Hình thức|Liên hệ)\s*:|$)",
         require_links=True, strict_detail=True, external_id_from_path=True),
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
    CareerFeed("Qualcomm", "https://careers.qualcomm.com/careers", "eightfold", {
        "domain": "qualcomm.com", "location_query": "Vietnam", "max_pages": 100,
    }),
    CareerFeed("NXP Vietnam", "https://nxp.wd3.myworkdayjobs.com/careers", "workday_vietnam", {
        "search_url": "https://nxp.wd3.myworkdayjobs.com/wday/cxs/nxp/careers/jobs",
        "detail_base": "https://nxp.wd3.myworkdayjobs.com/wday/cxs/nxp/careers",
        "search_texts": ["Vietnam", "Ho Chi Minh", "Hanoi"], "max_pages": 100,
    }),
    CareerFeed("Cadence Vietnam", "https://cadence.wd1.myworkdayjobs.com/External_Careers", "workday_vietnam", {
        "search_url": "https://cadence.wd1.myworkdayjobs.com/wday/cxs/cadence/External_Careers/jobs",
        "detail_base": "https://cadence.wd1.myworkdayjobs.com/wday/cxs/cadence/External_Careers",
        "search_texts": ["Vietnam", "Ho Chi Minh", "Hanoi"], "max_pages": 100,
    }),
    CareerFeed("NVIDIA", "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite", "workday", {
        "search_url": "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs",
        "detail_base": "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite",
        "search_text": "Vietnam",
    }),
    CareerFeed("Agoda", "https://careersatagoda.com/vacancies/", "agoda"),
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
    CareerFeed("VMO", "https://vmogroup.com/recruitment", "json_board", {
        "api_url": "https://api.vmogroup.com/api/v1/jobs", "params": {"status": "hiring", "sort_by": "date_created", "due_date": "true", "sort_order": "desc"},
        "rows_path": "data", "total_field": "total_item", "page_param": "page", "limit_param": "page_size",
        "id_field": "_id", "title_field": "title", "url_field": "url", "url_template": "https://vmogroup.com/recruitment/{title_url}",
        "description_sections": {"Responsibilities": "short_desc", "Requirements": "ideal_candidates", "Benefits": "benefits"},
        "location_fields": ["address", "location"], "location_include": "Vietnam", "mode_field": "working_model", "published_field": "date_created", "deadline_field": "due_date",
        "value_maps": {"location": {"vn": "Vietnam", "jp": "Japan"}, "working_model": {"REMOTE": "Remote", "HYBRID": "Hybrid", "VMO_OFFICE": "On-site"}},
    }),
    CareerFeed("Thoughtworks Vietnam", "https://www.thoughtworks.com/careers/vietnam", "json_board", {
        "api_url": "https://www.thoughtworks.com/rest/careers/jobs", "rows_path": "jobs",
        "id_field": "sourceSystemId", "title_field": "name", "url_field": "url", "url_template": "https://www.thoughtworks.com/careers/jobs/{sourceSystemId}",
        "location_fields": ["location", "country"], "location_include": "Vietnam", "mode_field": "locationType", "published_field": "updatedAt",
        "detail_selector": ".cmp-job-details",
    }),
    CareerFeed("Rikkeisoft", "https://tuyendung.rikkeisoft.com/recruitment", "json_board", {
        "api_url": "https://tuyendung.rikkeisoft.com/api/front/recruitment", "rows_path": "data.jobs.data", "total_field": "data.jobs.total",
        "page_param": "page", "limit_param": "size", "id_field": "_id", "title_field": "name", "url_field": "url",
        "url_template": "https://tuyendung.rikkeisoft.com/recruitment/detail/{slug}", "closed_fields": ["expired"], "deadline_field": "end_date",
        "description_sections": {"Responsibilities": "description", "Requirements": "qualifications", "Benefits": "benefits"},
        "location_fields": ["places"], "location_include": "Toàn quốc|Hà Nội|Hồ Chí Minh|Đà Nẵng|Việt Nam|Hải Phòng|Bình Dương|Hà Nam",
        "published_field": "start_date",
    }),
    vingroup("Vingroup"),
    vingroup("VinSmart Future", "45015271"),
    vingroup("VinFast", "45054894"),
    vingroup("Vinhomes", "45002926"),
    vingroup("Vinpearl", "45001013"),
    CareerFeed("Sky Mavis", "https://jobs.ashbyhq.com/skymavis", "json_board", {
        "api_url": "https://api.ashbyhq.com/posting-api/job-board/skymavis", "rows_path": "jobs", "title_field": "title", "url_field": "jobUrl",
        "required_fields": {"isListed": True}, "description_sections": {"": "descriptionHtml"}, "location_fields": ["location", "secondaryLocations"],
        "location_include": "Vietnam", "mode_field": "workplaceType", "published_field": "publishedAt", "apply_field": "applyUrl",
    }),
    html("FPT Digital", "https://digital.fpt.com/tuyen-dung", r"^/career/[^/?#]+/?$", listing_card_class="item", listing_title_selector=".title",
         description_selector=".fdx-single-career--content", location_selector=".career--location-content .p-place"),
    html("FPT Semiconductor", "https://fpt-semiconductor.com/careers/", r"^/career/[^/?#]+/?$",
         description_selector=".career-details-2", location_selector=".career-details-1 .grid-box .item:nth-child(4) .value", max_results=150),
    html("Finhay", "https://www.finhay.com.vn/nguon-nhan-luc?tab=tuyendung", r"^/co-hoi-nghe-nghiep/[^/?#]+/?$", description_selector=".payload-richtext", location_selector='.payload-richtext li:-soup-contains("Địa điểm làm việc:")'),
    html("SmartPay", "https://www.smartpayvn.com/tuyen-dung/", r"^/tuyen-dung-[^/?#]+/?$", listing_card_class="job-item-layout", listing_title_selector=".job-title",
         max_pages=10, description_selector=".post-content", location_selector=".section-post-detail .post-location"),
    CareerFeed("NAVER Vietnam", "https://navercorp.vn/en/recruitment.html", "json_board", {
        "api_url": "https://boards-api.greenhouse.io/v1/boards/navervietnam/jobs", "params": {"content": "true"}, "rows_path": "jobs", "total_field": "meta.total",
        "title_field": "title", "url_field": "absolute_url", "description_sections": {"": "content"}, "location_fields": ["location.name"],
        "location_include": "Vietnam", "published_field": "first_published",
    }),
    CareerFeed("Trusting Social", "https://trustingsocial.com/careers/", "json_board", {
        "api_url": "https://trustingsocial.com/page-data/careers/page-data.json", "rows_path": "result.data.allRecruiterboxOpening.nodes",
        "id_field": "rawID", "title_field": "title", "url_field": "url", "url_template": "https://trustingsocial.com/careers/openings/{slug}",
        "required_fields": {"state": "Published"}, "detail_api_url": "https://trustingsocial.com/page-data/careers/openings/{slug}/page-data.json",
        "detail_rows_path": "result.data.recruiterboxOpening", "description_sections": {"": "description"},
        "location_fields": ["location.city", "location.country"], "location_include": "Vietnam", "apply_field": "hosted_url",
    }),
    CareerFeed("Cake by VPBank", "https://cake.vn/tuyen-dung", "json_board", {
        "api_url": "https://home.internal.cake.vn/api/ats/public/job-post/all", "rows_path": "data", "title_field": "title", "url_field": "url",
        "url_template": "https://cake.vn/tuyen-dung/jobs/{key}", "description_sections": {"": "job_content"},
        "location_fields": ["office.name", "office.address"], "published_field": "created_at", "closed_fields": ["is_deleted"],
    }),
    CareerFeed("Tek Experts", "https://careers.teknowledge.com/teknowledge/jobs", "json_board", {
        "api_url": "https://careers.teknowledge.com/api/jobs", "params": {"country": "Vietnam"}, "rows_path": "jobs", "row_path": "data", "total_field": "totalCount",
        "page_param": "page", "limit_param": "limit", "id_field": "req_id", "title_field": "title", "url_field": "url",
        "url_template": "https://careers.teknowledge.com/tek-experts/jobs/{slug}?lang=en-us", "description_sections": {"": "description"},
        "location_fields": ["city", "country"], "published_field": "posted_date", "apply_field": "apply_url",
    }),
    html("Timo", "https://timo.vn/careers/", r"^/career/[^/?#]+/?$", description_selector=".tr-single-career__content",
         location_selector=".tr-single-career__meta-item:nth-child(3)", max_results=100),
    CareerFeed("Vikki Bank", "https://vikkibank.vn/tuyen-dung/", "vikki_bank", {"max_pages": 24}),
    CareerFeed("Co-opBank", "https://co-opbank.vn/tuyen-dung/", "bank_notices", {
        "notice_kind": "coopbank", "link_path": r"^/tuyen-dung/[^/?#]+/?$", "max_pages": 8,
    }),
    ainavi("Eximbank", "tuyendungeximbank.com"),
    ainavi("MCredit", "tuyendungmcredit.ainavi.com.vn"),
    CareerFeed("VPS", "https://www.vps.com.vn/gia-nhap-vps/co-hoi-nghe-nghiep", "json_board", {
        "api_url": "https://www.vps.com.vn/api/v1/posts", "params": {"type": 3, "language": "vi"}, "rows_path": "data.results", "total_field": "data.total",
        "page_param": "page", "limit_param": "pageSize", "title_field": "contents.0.title", "url_field": "url",
        "template_fields": {"slug": "contents.0.slug"}, "url_template": "https://www.vps.com.vn/gia-nhap-vps/co-hoi-nghe-nghiep/chi-tiet-cong-viec/{slug}",
        "detail_api_url": "https://www.vps.com.vn/api/v1/posts/{slug}", "detail_api_params": {"language": "vi"}, "detail_rows_path": "data.results",
        "description_sections": {"": "contents.0.content"}, "location_fields": ["recruitmentContent.locations"],
        "published_field": "publishedAt", "deadline_field": "recruitmentContent.endTime",
    }),
    workday("Hong Leong Bank Vietnam", "hlb.wd3.myworkdayjobs.com", "hlb", "HLBCareers", "locationCountry"),
    workday("DBS", "dbs.wd3.myworkdayjobs.com", "dbs", "DBS_Careers", "locationCountry"),
    workday("Citi Vietnam", "citi.wd5.myworkdayjobs.com", "citi", "2", "Country_and_Jurisdiction"),
    workday("Deutsche Bank", "db.wd3.myworkdayjobs.com", "db", "DBWebsite", "Country"),
    workday("MUFG", "mufgub.wd3.myworkdayjobs.com", "mufgub", "MUFG-Careers", "Country"),
    html("Woori Bank Vietnam", "https://woori.com.vn/thong-tin-tuyen-dung/", r"^/tuyen-dung/[^/?#]+/?$", pagination="wordpress", max_pages=6,
         description_selector="#content-main", location_selector=".content-main--item:first-child .content-main--item__right"),
    CareerFeed("Public Bank Vietnam", "https://publicbank.com.vn/News/Career?cateId=22", "public_bank"),
    CareerFeed("ANZ Vietnam", "https://careers.anz.com/search/?q=&locationsearch=Vietnam", "successfactors", {"max_pages": 4, "search_page_size": 25}),
    CareerFeed("SMBC", "https://careerasia.smbc.co.jp/SMBC/search/?q=&locationsearch=Viet+Nam", "successfactors", {
        "max_pages": 4, "search_page_size": 25, "location_selector": "[data-careersite-propertyid=customfield1]",
    }),
    CareerFeed("CIMB Vietnam", "https://ejox.fa.ap1.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_11", "json_board", {
        "api_url": "https://ejox.fa.ap1.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions",
        "params": {"onlyData": "true", "expand": "requisitionList"}, "params_templates": {"finder": "findReqs;siteNumber=CX_11,limit={limit},offset={offset}"},
        "rows_path": "items.0.requisitionList", "total_field": "items.0.TotalJobsCount", "pagination": "offset", "id_field": "Id", "title_field": "Title", "url_field": "url",
        "url_template": "https://ejox.fa.ap1.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_11/job/{Id}", "required_fields": {"PrimaryLocationCountry": "VN"},
        "detail_api_url": "https://ejox.fa.ap1.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails", "detail_api_params": {"onlyData": "true", "expand": "all", "finder": "ById;Id={Id}"},
        "detail_rows_path": "items.0", "description_sections": {"": "ExternalDescriptionStr"}, "location_fields": ["PrimaryLocation"],
        "published_field": "ExternalPostedStartDate", "deadline_field": "ExternalPostedEndDate",
    }),
    # Honda detail pages have stable numeric IDs in their query strings.
    html("Honda Vietnam", "https://www.honda.com.vn/tuyen-dung/co-hoi-nghe-nghiep",
         r"^/tuyen-dung/chi-tiet-co-hoi-nghe-nghiep$", keep_query=True,
         max_results=120, description_selector="main, article"),
    # Hyundai Thanh Cong is the Hyundai Motor joint venture in Vietnam, not Hyundai globally.
    html("Hyundai Motor Vietnam", "https://career.hyundai.thanhcong.vn/job-search/all-jobs/en",
         r"^/job/[^/?#]+\.html$", max_results=120,
         description_selector="main, article, .job-detail"),
    html("BNP Paribas", "https://apac.bnpparibas/en/work-with-us/apply-now/our-opportunities/?city%5B%5D=ho-chi-minh&city%5B%5D=ho-chi-minh-city", r"^/en/jobs/[^/?#]+/?$",
         description_selector=".jobs__content", location_selector=".jobs__details .crd__specs li:nth-child(2) span"),

    # Agent 6: finance and insurance. The employer identity is kept even on a group ATS.
    html("Mirae Asset Vietnam", "https://recruitment.mafc.vn/job-search/all-jobs/en",
         r"^/(?:job|viec-lam)/[^/?#]+\.[a-f0-9]+\.html$", max_pages=8, max_results=120,
         description_selector="[class*=job-description], [class*=job-detail], main"),
    html("MB Ageas Life", "https://tuyendung.mblife.vn/job-search/all-jobs/en",
         r"^/job/[^/?#]+\.[a-f0-9]+\.html$", max_pages=8, max_results=120,
         description_selector="[class*=job-description], [class*=job-detail], main"),
    CareerFeed("Bao Viet", "https://www.baoviet.com.vn/vi/tuyen-dung", "baoviet_announcement"),
    CareerFeed("FWD Vietnam", "https://fwd.wd3.myworkdayjobs.com/FWDcareersite", "workday", {
        "search_url": "https://fwd.wd3.myworkdayjobs.com/wday/cxs/fwd/FWDcareersite/jobs",
        "detail_base": "https://fwd.wd3.myworkdayjobs.com/wday/cxs/fwd/FWDcareersite",
        "search_text": "Vietnam", "location_include": r"Vietnam|Việt Nam",
    }),
    CareerFeed("Prudential Vietnam", "https://prudential.wd3.myworkdayjobs.com/prudential", "workday", {
        "search_url": "https://prudential.wd3.myworkdayjobs.com/wday/cxs/prudential/prudential/jobs",
        "detail_base": "https://prudential.wd3.myworkdayjobs.com/wday/cxs/prudential/prudential",
        "search_text": "Vietnam", "location_include": r"Vietnam|Việt Nam",
    }),
    CareerFeed("Manulife Vietnam", "https://manulife.wd3.myworkdayjobs.com/MFCJH_Jobs", "workday", {
        "search_url": "https://manulife.wd3.myworkdayjobs.com/wday/cxs/manulife/MFCJH_Jobs/jobs",
        "detail_base": "https://manulife.wd3.myworkdayjobs.com/wday/cxs/manulife/MFCJH_Jobs",
        "search_text": "Vietnam", "location_include": r"Vietnam|Việt Nam",
    }),
    CareerFeed("AIA Vietnam", "https://aia.wd3.myworkdayjobs.com/External", "workday", {
        "search_url": "https://aia.wd3.myworkdayjobs.com/wday/cxs/aia/External/jobs",
        "detail_base": "https://aia.wd3.myworkdayjobs.com/wday/cxs/aia/External",
        "search_text": "Vietnam", "location_include": r"Vietnam|Việt Nam|VN-AIA",
    }),

    # Agent 9: employer-operated, country-scoped first-party portals.
    html("Ampere", "https://careers.amperecomputing.com/search/jobs/in/sg-ho-chi-minh-sai-gon",
         r"^/jobs/\d+-[^/?#]+$", max_results=60, description_selector="main",
         external_id_pattern=r"/jobs/(\d+)-", location_text_pattern=r"(?m)^Location:\s*([^\n]+)"),
    CareerFeed("SAP Vietnam", "https://jobs.sap.com/go/Vietnam/9513801/", "successfactors", {
        "max_pages": 2,
        "location_selector": ".job-location, #job-location, [data-careersite-propertyid=location]",
    }),
)
