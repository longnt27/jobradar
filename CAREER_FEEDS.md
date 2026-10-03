# Direct company career feeds

Job Radar checks these 43 verified company career listings every four hours. Each entry points to an employer career page or its dedicated recruiting portal. A listing is stored only when its own job posting has a relevant title and description. An empty scan means no matching open posting was found by that adapter at scan time; it does not imply the company has no vacancies.

| Company | Career page | Adapter |
| --- | --- | --- |
| VinAI | [Open careers](https://www.vinai.io/careers/) | HTML links |
| VinDynamics | [Open careers](https://vindynamics.net/career) | Company career API |
| VinRobotics | [Open careers](https://vinrobotics.net/career) | Company career API |
| VinMotion | [Open careers](https://vinmotion.net/vi/career) | HTML board |
| VinUni | [Open careers](https://vinuni.talent.vn/jobs) | HTML board |
| Viettel Group | [Open careers](https://jobs.viettel.vn/search/?q=AI) | SuccessFactors |
| FPT Education | [Open careers](https://career.fpt.edu.vn/Job/Search) | HTML board |
| FPT Telecom | [Open careers](https://fptjobs.com/tuyen-dung) | Browser board |
| Techcombank | [Open careers](https://www.techcombankjobs.com/go/Data-%26-Analytics/549144/) | SuccessFactors |
| Vietcombank | [Open careers](https://tuyendung.vietcombank.com.vn/go/IT/572944/) | SuccessFactors |
| MB Bank | [Open careers](https://careers.mbbank.com.vn/list-of-posts) | Company career API |
| BIDV | [Open careers](https://tuyendung.bidv.com.vn/danh-sach-viec-lam-moi.html) | Company career API |
| VietinBank | [Open careers](https://tuyendung.vietinbank.vn/tuyendung/tuyen-dung) | Company career API |
| TPBank | [Open careers](https://tuyendung.tpb.vn/vi/jobs) | Browser board |
| LPBank | [Open careers](https://tuyendung.lpbank.com.vn/vi/jobs) | Browser board |
| MSB | [Open careers](https://jobs.msb.com.vn/latest-jobs) | HTML board |
| SeABank | [Open careers](https://tuyendung.seabank.com.vn/jobs?page=1) | HTML board |
| BaoViet Bank | [Open careers](https://www.baovietbank.vn/tuyen-dung/) | HTML board |
| ABBank | [Open careers](https://careers.abbank.vn/jobs) | HTML board |
| PGBank | [Open careers](https://tuyendung.pgbank.com.vn/) | HTML board |
| VietABank | [Open careers](https://tuyendung.vietabank.com.vn/vi/jobs) | Browser board |
| MBV | [Open careers](https://www.mbv.com.vn/tuyen-dung/co-hoi-nghe-nghiep) | Browser board |
| VPBank | [Open careers](https://vpbank.talent.vn/jobs?dept=3326) | HTML board |
| GPBank | [Open careers](https://gpbank.talent.vn/jobs?dept=1538) | HTML board |
| CMC Global | [Open careers](https://cmcglobal.com.vn/career/) | HTML board |
| CMC TS | [Open careers](https://careers.cmcts.com.vn/) | HTML board |
| CMC Telecom | [Open careers](https://cmctelecom.vn/danh-sach-tuyen-dung/) | HTML board |
| NTQ Solution | [Open careers](https://career.ntq.com.vn/careers) | HTML board |
| VNPAY | [Open careers](https://tuyendung.vnpay.vn/) | HTML board |
| Base.vn | [Open careers](https://baseinc.talent.vn/alljobs?dept=9) | HTML board |
| VNDIRECT | [Open careers](https://vndirect.talent.vn/jobs) | HTML board |
| TeenCare | [Open careers](https://teencare.talent.vn/) | HTML board |
| Golden Gate | [Open careers](https://ggg.talent.vn/) | HTML board |
| Eastgate Software | [Open careers](https://eastgatesoftware.talent.vn/jobs) | HTML board |
| SmartOSC | [Open careers](https://careers.smartrecruiters.com/SmartOSC) | Company recruiting portal |
| KiotViet | [Open careers](https://about.kiotviet.vn/cong-viec/) | HTML board |
| MISA | [Open careers](https://www.misa.vn/tuyen-dung/) | HTML board |
| Sapo | [Open careers](https://tuyendung.sapo.vn/) | HTML board |
| Cốc Cốc | [Open careers](https://careers.coccoc.com/jobs) | Browser board |
| VNPT AI | [Open careers](https://tuyendung.vnpt.vn/viec-lam/don-vi-cong-ty-vnpt-ai-d3678304.html) | HTML board |
| VNPT IT | [Open careers](https://tuyendung.vnpt.vn/viec-lam/don-vi-cong-ty-cong-nghe-thong-tin-vnpt-d9621.html) | HTML board |
| VNPT Net | [Open careers](https://tuyendung.vnpt.vn/viec-lam/don-vi-tong-cong-ty-ha-tang-mang-d8381.html) | HTML board |
| VNPT Media | [Open careers](https://tuyendung.vnpt.vn/viec-lam/don-vi-tong-cong-ty-truyen-thong-d8606.html) | HTML board |

The [employer directory](job_radar/seeds.py) contains 181 companies after the [HCMC-based exclusions](EMPLOYER_SCOPE.md). Its other 138 entries are leads without an active built-in feed. Their presence in the directory is not a claim that their jobs are collected. Add a verified direct career page from an employer card. General job aggregators are not part of this catalog.

Feed definitions and selectors: [job_radar/feed_catalog.py](job_radar/feed_catalog.py). Collector implementations: [job_radar/collectors.py](job_radar/collectors.py). Older postings with an explicit past deadline are skipped. The app creates no placeholder vacancies.
