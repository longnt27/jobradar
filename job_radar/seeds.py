from __future__ import annotations

import json
from urllib.parse import urlencode

from .db import Database, new_id, now
from .feed_catalog import CAREER_FEEDS
from .employer_scope import HCMC_BASED


EMPLOYER_DIRECTORY: dict[str, str] = {
    "Vingroup": "vingroup", "VinFast": "vingroup", "VinSmart Future": "vingroup",
    "VinRobotics": "vingroup", "VinMotion": "vingroup", "VinDynamics": "vingroup",
    "VinSpace": "vingroup", "VinSOC": "vingroup", "VinCSS": "vingroup",
    "VinAI": "vingroup", "GSM / Xanh SM": "vingroup", "V-GREEN": "vingroup",
    "VinBus": "vingroup", "VinUni": "vingroup", "Vinmec": "vingroup",
    "Vinschool": "vingroup", "Vinhomes": "vingroup", "Vinpearl": "vingroup",
    "VinHMS": "vingroup", "VinID": "vingroup", "MovianAI": "technology",
    "One Mount": "technology", "Viettel Group": "viettel", "Viettel High Tech": "viettel",
    "Viettel Cyber Security": "viettel", "Viettel Solutions": "viettel",
    "Viettel Digital": "viettel", "Viettel Software": "viettel",
    "Viettel Telecom": "viettel", "Viettel IDC": "viettel", "Viettel AI": "viettel",
    "Viettel Research and Development": "viettel",
    "VNPT Group": "vnpt", "VNPT AI": "vnpt", "VNPT IT": "vnpt",
    "VNPT Technology": "vnpt", "VNPT Net": "vnpt", "VNPT Media": "vnpt",
    "FPT Corporation": "fpt", "FPT Software": "fpt", "FPT Smart Cloud": "fpt",
    "FPT Digital": "fpt", "FPT IS": "fpt", "FPT Telecom": "fpt",
    "FPT Online": "fpt", "FPT Education": "fpt", "FPT Semiconductor": "fpt",
    "FPT University": "fpt", "FPT.AI": "fpt",
    "CMC Corporation": "cmc", "CMC Global": "cmc", "CMC TS": "cmc",
    "CMC Telecom": "cmc", "CMC Cloud": "cmc", "CMC University": "cmc",
    "Agribank": "bank", "Vietcombank": "bank", "BIDV": "bank",
    "VietinBank": "bank", "MB Bank": "bank", "Techcombank": "bank",
    "VPBank": "bank", "ACB": "bank", "Sacombank": "bank", "SHB": "bank",
    "HDBank": "bank", "TPBank": "bank", "VIB": "bank", "MSB": "bank",
    "SeABank": "bank", "OCB": "bank", "Eximbank": "bank", "LPBank": "bank",
    "Nam A Bank": "bank", "NCB": "bank", "ABBank": "bank", "Bac A Bank": "bank",
    "BaoViet Bank": "bank", "BVBank": "bank", "VietABank": "bank",
    "VietBank": "bank", "KienlongBank": "bank", "PGBank": "bank",
    "Saigonbank": "bank", "SCB": "bank", "PVcomBank": "bank",
    "VCBNeo": "bank", "MBV": "bank", "GPBank": "bank", "Vikki Bank": "bank",
    "Co-opBank": "bank", "Vietnam Bank for Social Policies": "bank",
    "HSBC Vietnam": "foreign_bank", "Standard Chartered Vietnam": "foreign_bank",
    "Shinhan Bank Vietnam": "foreign_bank", "Woori Bank Vietnam": "foreign_bank",
    "UOB Vietnam": "foreign_bank", "CIMB Vietnam": "foreign_bank",
    "Public Bank Vietnam": "foreign_bank", "Hong Leong Bank Vietnam": "foreign_bank",
    "ANZ Vietnam": "foreign_bank", "Citi Vietnam": "foreign_bank",
    "DBS": "foreign_bank", "MUFG": "foreign_bank", "Mizuho": "foreign_bank",
    "SMBC": "foreign_bank", "ICBC": "foreign_bank", "Bank of China": "foreign_bank",
    "KEB Hana": "foreign_bank", "BNP Paribas": "foreign_bank",
    "Deutsche Bank": "foreign_bank", "Maybank": "foreign_bank",
    "Bangkok Bank": "foreign_bank", "MoMo": "fintech", "ZaloPay": "fintech",
    "MCredit": "finance", "MB Ageas Life": "insurance",
    "VNPAY": "fintech", "NAPAS": "fintech", "Payoo": "fintech",
    "OnePay": "fintech", "SmartPay": "fintech", "9Pay": "fintech",
    "Moca": "fintech", "Trusting Social": "fintech", "Cake by VPBank": "fintech",
    "Timo": "fintech", "Finhay": "fintech", "TCBS": "finance",
    "SSI": "finance", "VNDIRECT": "finance", "VPS": "finance",
    "FE Credit": "finance", "Home Credit Vietnam": "finance", "HD SAISON": "finance",
    "FWD Vietnam": "insurance", "Prudential Vietnam": "insurance",
    "Manulife Vietnam": "insurance", "AIA Vietnam": "insurance",
    "Chubb Life Vietnam": "insurance", "Mirae Asset Vietnam": "finance", "Bao Viet": "finance",
    "Shopee": "commerce", "SeaMoney": "commerce", "SPX Express": "commerce",
    "Lazada": "commerce", "Tiki": "commerce", "Grab": "commerce",
    "Be Group": "commerce", "Traveloka": "commerce", "Agoda": "commerce",
    "Booking.com": "commerce", "NAVER Vietnam": "technology", "Zalo AI": "technology",
    "VNG": "technology", "VNG Cloud": "technology", "Cinnamon AI": "technology",
    "Rikkeisoft": "technology", "TMA Solutions": "technology",
    "Rikkei AI": "technology", "Gear Inc": "technology",
    "KMS Technology": "technology", "NashTech Vietnam": "technology",
    "Axon Active": "technology", "NTQ Solution": "technology",
    "Sun Asterisk": "technology", "VMO": "technology", "Sotatek": "technology",
    "SmartOSC": "technology", "Tek Experts": "technology",
    "EPAM Vietnam": "technology", "Thoughtworks Vietnam": "technology",
    "Anduin Transactions": "technology", "ELSA": "technology",
    "Katalon": "technology", "KiotViet": "technology", "Base.vn": "technology",
    "MISA": "technology", "Sapo": "technology", "Haravan": "technology",
    "Cốc Cốc": "technology", "Sky Mavis": "technology", "Amanotes": "technology",
    "Hanoi University of Science and Technology": "research",
    "VNU University of Engineering and Technology": "research",
    "Posts and Telecommunications Institute of Technology": "research",
    "Phenikaa University": "research", "NVIDIA": "global_tech", "Qualcomm": "global_tech",
    "Samsung R&D Vietnam": "global_tech", "Samsung Electronics Vietnam": "global_tech",
    "LG Electronics R&D Vietnam": "global_tech", "Bosch Vietnam": "global_tech",
    "Intel Products Vietnam": "global_tech", "NXP Vietnam": "global_tech",
    "Renesas Vietnam": "global_tech", "Synopsys Vietnam": "global_tech",
    "Cadence Vietnam": "global_tech", "Marvell Vietnam": "global_tech",
    "MediaTek Vietnam": "global_tech", "ARM": "global_tech", "Ampere": "global_tech",
    "Microsoft Vietnam": "global_tech", "Google Vietnam": "global_tech",
    "Amazon / AWS Vietnam": "global_tech", "IBM Vietnam": "global_tech",
    "Oracle Vietnam": "global_tech", "SAP Vietnam": "global_tech",
    "Cisco Vietnam": "global_tech", "Ericsson Vietnam": "global_tech",
    "Nokia Vietnam": "global_tech", "Huawei Vietnam": "global_tech",
    "Panasonic Vietnam": "global_tech", "Toyota Vietnam": "global_tech",
    "Honda Vietnam": "global_tech", "Hyundai Motor Vietnam": "global_tech",
    "TeenCare": "technology", "Golden Gate": "commerce",
    "Cloud Ace": "technology", "Eastgate Software": "technology",
    "Innovature BPO": "technology", "TARA JSC": "commerce",
}

EMPLOYERS = {name: category for name, category in EMPLOYER_DIRECTORY.items() if name not in HCMC_BASED}

ROLE_TERMS = (
    "AI Engineer", "Applied AI Engineer", "Machine Learning Engineer",
    "Research Engineer", "AI Researcher", "LLM Engineer",
    "Agentic AI Engineer", "Computer Vision Engineer", "Generative AI Engineer",
)

EMPLOYER_ALIASES = {
    "GSM / Xanh SM": ["GreenSM", "Green SM", "Xanh SM", "GSM"],
    "Viettel Cyber Security": ["Viettel CyberSec", "Viettel Cyber Security Company"],
    "VinSmart Future": ["VinSmart Future JSC"],
    "FPT Smart Cloud": ["FPT Smart Cloud and AI"],
    "VNPT AI": ["VNPT-AI"],
}


def seed(db: Database) -> None:
    timestamp = now()
    with db.connection() as conn:
        # Keep historical scans for audit, but remove HCMC-based employers and
        # their sources from the active discovery surface on upgrade.
        for name in HCMC_BASED:
            conn.execute("UPDATE employers SET coverage_status='excluded_hcm',updated_at=? WHERE name=?", (timestamp, name))
            conn.execute("UPDATE sources SET enabled=0 WHERE employer_id IN (SELECT id FROM employers WHERE name=?)", (name,))
        for name, category in EMPLOYERS.items():
            conn.execute(
                "INSERT OR IGNORE INTO employers(id,name,category,created_at,updated_at) VALUES(?,?,?,?,?)",
                (new_id(), name, category, timestamp, timestamp),
            )
        for name, aliases in EMPLOYER_ALIASES.items():
            conn.execute("UPDATE employers SET aliases=? WHERE name=? AND aliases='[]'",
                         (json.dumps(aliases, ensure_ascii=False), name))
        for feed in CAREER_FEEDS:
            employer = conn.execute("SELECT id FROM employers WHERE name=?", (feed.employer,)).fetchone()
            if not employer:
                raise ValueError(f"Career feed has no employer: {feed.employer}")
            conn.execute("UPDATE employers SET career_url=COALESCE(career_url,?) WHERE id=?", (feed.url, employer[0]))
            config = json.dumps({"adapter": feed.adapter, **feed.options})
            existing_feed = conn.execute(
                "SELECT id,config FROM sources WHERE kind='career' AND employer_id=? AND url=?",
                (employer[0], feed.url),
            ).fetchone()
            if existing_feed:
                if existing_feed[1] != config:
                    conn.execute("UPDATE sources SET config=? WHERE id=?", (config, existing_feed[0]))
            else:
                conn.execute(
                    "INSERT INTO sources(id,kind,name,url,employer_id,interval_minutes,config,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (new_id(), "career", f"{feed.employer} careers", feed.url, employer[0], 240, config, timestamp),
                )
        existing = conn.execute("SELECT COUNT(*) FROM sources WHERE kind='linkedin'").fetchone()[0]
        for role in ROLE_TERMS:
            for location in ("Hanoi, Vietnam", "Vietnam", "Remote"):
                name = f"{role} · {location}"
                # LinkedIn's AI search currently drops the separate location
                # parameter. Put the location in the query it actually uses.
                keywords = f"{role} remote" if location == "Remote" else f"{role} in {location}"
                url = f"https://www.linkedin.com/jobs/search/?{urlencode({'keywords': keywords})}"
                previous_query = {"keywords": role, "location": location}
                if location == "Remote":
                    previous_query["f_WT"] = "2"
                previous_url = f"https://www.linkedin.com/jobs/search/?{urlencode(previous_query)}"
                # The original URLs put f_TPR before f_WT for remote searches.
                old_query = {"keywords": role, "location": location, "f_TPR": "r86400"}
                if location == "Remote":
                    old_query["f_WT"] = "2"
                old_url = f"https://www.linkedin.com/jobs/search/?{urlencode(old_query)}"
                if not existing:
                    conn.execute(
                        "INSERT INTO sources(id,kind,name,url,config,created_at) VALUES(?,?,?,?,?,?)",
                        (new_id(), "linkedin", name, url,
                         json.dumps({"max_results": 150}), timestamp),
                    )
                    continue
                saved = conn.execute(
                    "SELECT id,config FROM sources WHERE kind='linkedin' AND name=? AND url IN (?,?)",
                    (name, old_url, previous_url),
                ).fetchone()
                if saved:
                    config = json.loads(saved[1])
                    if config.get("max_results", 40) == 40:
                        config["max_results"] = 150
                    conn.execute(
                        "UPDATE sources SET url=?,config=?,last_attempt_at=NULL,last_success_at=NULL,"
                        "last_status=NULL WHERE id=?",
                        (url, json.dumps(config), saved[0]),
                    )
    if db.get_setting("profile") is None:
        db.set_setting("profile", {
            "name": "", "email": "", "phone": "", "location": "", "drafting_provider": "",
            "summary": "", "skills": [], "experience": [], "education": [],
            "links": [], "preferences": {"roles": list(ROLE_TERMS), "remote": True},
        })
