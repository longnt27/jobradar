from __future__ import annotations

import json
from urllib.parse import urlencode

from .db import Database, new_id, now


EMPLOYERS: dict[str, str] = {
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
}

ROLE_TERMS = (
    "AI Engineer", "Applied AI Engineer", "Machine Learning Engineer",
    "Research Engineer", "AI Researcher", "LLM Engineer",
    "Agentic AI Engineer", "Computer Vision Engineer", "Generative AI Engineer",
)


def seed(db: Database) -> None:
    timestamp = now()
    with db.connection() as conn:
        for name, category in EMPLOYERS.items():
            conn.execute(
                "INSERT OR IGNORE INTO employers(id,name,category,created_at,updated_at) VALUES(?,?,?,?,?)",
                (new_id(), name, category, timestamp, timestamp),
            )
        vinai = conn.execute("SELECT id FROM employers WHERE name='VinAI'").fetchone()
        if vinai:
            career_url = "https://www.vinai.io/careers/"
            conn.execute("UPDATE employers SET career_url=COALESCE(career_url,?) WHERE id=?", (career_url, vinai[0]))
            if not conn.execute("SELECT id FROM sources WHERE kind='career' AND employer_id=? AND url=?", (vinai[0], career_url)).fetchone():
                conn.execute("INSERT INTO sources(id,kind,name,url,employer_id,interval_minutes,created_at) VALUES(?,?,?,?,?,?,?)",
                             (new_id(), "career", "VinAI careers", career_url, vinai[0], 240, timestamp))
        existing = conn.execute("SELECT COUNT(*) FROM sources WHERE kind='linkedin'").fetchone()[0]
        if not existing:
            for role in ROLE_TERMS:
                for location in ("Hanoi, Vietnam", "Vietnam", "Remote"):
                    query = {"keywords": role, "location": location, "f_TPR": "r86400"}
                    if location == "Remote":
                        query["f_WT"] = "2"
                    conn.execute(
                        "INSERT INTO sources(id,kind,name,url,config,created_at) VALUES(?,?,?,?,?,?)",
                        (new_id(), "linkedin", f"{role} · {location}",
                         f"https://www.linkedin.com/jobs/search/?{urlencode(query)}",
                         json.dumps({"max_results": 40}), timestamp),
                    )
    if db.get_setting("profile") is None:
        db.set_setting("profile", {
            "name": "", "email": "", "phone": "", "location": "Hanoi, Vietnam",
            "summary": "", "skills": [], "experience": [], "education": [],
            "links": [], "preferences": {"roles": list(ROLE_TERMS), "remote": True},
        })
