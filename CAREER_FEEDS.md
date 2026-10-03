# Company career feeds

Job Radar registers these 31 company-specific career listings. Each source runs every four hours. The collector follows only that company's posting links and stores the original posting URL. A company-branded recruiting portal is included when it is the company's application site; general job aggregators and social posts are not part of this catalog.

| Company | Career listing | Collector |
| --- | --- | --- |
| VinAI | [VinAI careers](https://www.vinai.io/careers/) | HTML |
| VinDynamics | [VinDynamics careers](https://vindynamics.net/career) | Company career API |
| VinRobotics | [VinRobotics careers](https://vinrobotics.net/career) | Company career API |
| VinUni | [VinUni careers](https://vinuni.talent.vn/jobs) | Company recruiting portal |
| Viettel Group | [Viettel careers](https://jobs.viettel.vn/search/?q=AI) | SuccessFactors |
| FPT Education | [FPT Education careers](https://career.fpt.edu.vn/Job/Search) | HTML |
| Techcombank | [Techcombank Data & Analytics](https://www.techcombankjobs.com/go/Data-%26-Analytics/549144/) and Technology | SuccessFactors |
| Vietcombank | [Vietcombank IT](https://tuyendung.vietcombank.com.vn/go/IT/572944/) and branch IT | SuccessFactors |
| MB Bank | [MB Bank careers](https://careers.mbbank.com.vn/list-of-posts) | Company career API |
| VPBank | [VPBank data and analytics careers](https://vpbank.talent.vn/jobs?dept=3326) | Company recruiting portal |
| GPBank | [GPBank technology careers](https://gpbank.talent.vn/jobs?dept=1538) | Company recruiting portal |
| ACB | [ACB careers](https://acbjobs.talent.vn/jobs) | Company recruiting portal |
| MoMo | [MoMo careers](https://momo.careers/jobs-opening) | HTML |
| Katalon | [Katalon careers](https://careers.katalon.com/) | HTML |
| ELSA | [ELSA careers](https://elsaspeak.com/en/career) | Company recruiting portal |
| CMC Global | [CMC Global careers](https://cmcglobal.com.vn/career/) | HTML with pagination |
| CMC TS | [CMC TS careers](https://careers.cmcts.com.vn/) | Company recruiting portal |
| TMA Solutions | [TMA careers](https://www.tma.vn/tuyen-dung/viec-lam) | HTML |
| NTQ Solution | [NTQ careers](https://career.ntq.com.vn/careers) | HTML |
| Axon Active | [Axon Active careers](https://www.careers.axonactive.com/) | HTML |
| VNPAY | [VNPAY careers](https://tuyendung.vnpay.vn/) | HTML |
| Base.vn | [Base careers](https://baseinc.talent.vn/alljobs?dept=9) | Company recruiting portal |
| VNDIRECT | [VNDIRECT careers](https://vndirect.talent.vn/jobs) | Company recruiting portal |
| TeenCare | [TeenCare careers](https://teencare.talent.vn/) | Company recruiting portal |
| Golden Gate | [Golden Gate careers](https://ggg.talent.vn/) | Company recruiting portal |
| Cloud Ace | [Cloud Ace careers](https://cloudace.talent.vn/) | Company recruiting portal |
| Eastgate Software | [Eastgate careers](https://eastgatesoftware.talent.vn/jobs) | Company recruiting portal |
| Innovature BPO | [Innovature careers](https://innovaturebpo.talent.vn/jobs) | Company recruiting portal |
| TARA JSC | [TARA careers](https://tara.talent.vn/jobs) | Company recruiting portal |
| KMS Technology | [KMS careers](https://careers.smartrecruiters.com/kmstechnology1) | SmartRecruiters company feed |
| SmartOSC | [SmartOSC careers](https://careers.smartrecruiters.com/SmartOSC) | SmartRecruiters company feed |

The source catalog and adapter settings live in [job_radar/feed_catalog.py](job_radar/feed_catalog.py). A feed can be empty when there are no current matching roles. Postings with an explicit past application deadline are skipped. SmartRecruiters postings older than 180 days are skipped. The app does not create placeholder vacancies.
