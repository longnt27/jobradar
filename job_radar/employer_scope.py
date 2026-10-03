"""Employer location scope for the built-in directory and career feeds.

These are companies whose headquarters or primary Vietnam office is in HCMC.
The URLs are first-party evidence for the classification. An office or an
individual vacancy in HCMC is not enough to exclude an employer.
"""

HCMC_BASED: dict[str, str] = {
    "ACB": "https://acb.com.vn/en/about-us/inauguration-of-acb-headquarters-building-in-ho-chi-minh-city",
    "Sacombank": "https://www.sacombank.com.vn/",
    "HDBank": "https://hdbank.com.vn/",
    "VIB": "https://www.vib.com.vn/vn/tin-tuc/he-thong/thong-bao-vib-thay-doi-dia-diem-tru-so-chinh",
    "OCB": "https://www.ocb.com.vn/",
    "Nam A Bank": "https://www.namabank.com.vn/",
    "BVBank": "https://bvbank.net.vn/tin-tuc/chuc-mung-khach-hang-trung-thuong-mo-nam-khoi-sac/",
    "VietBank": "https://www.vietbank.com.vn/",
    "Saigonbank": "https://www.saigonbank.com.vn/",
    "SCB": "https://www.scb.com.vn/vie/tin-tuc/scb-khai-truong-phong-dich-vu-khach-hang-trung-tam",
    "MoMo": "https://www.momo.vn/ve-xe/xe-phu-quy-buslines",
    "ZaloPay": "https://zalopay.vn/",
    "VNG": "https://vng.com.vn/aboutvng.html?lang=en",
    "Zalo AI": "https://vng.com.vn/aboutvng.html?lang=en",
    "VNG Cloud": "https://vng.com.vn/aboutvng.html?lang=en",
    "TMA Solutions": "https://www.tma.vn/Lien-he",
    "KMS Technology": "https://kms-technology.com/contact/",
    "Axon Active": "https://axonactive.com/imprint/",
    "Katalon": "https://careers.katalon.com/jobs/8102435-product-security-engineer-cloud-ops-focus/applications/new",
    "Haravan": "https://www.haravan.com/pages/about/",
    "Be Group": "https://be.com.vn/hoi-chu-shop-gold",
    "Tiki": "https://tiki.vn/",
    "Lazada": "https://pages.lazada.vn/wow/i/vn/gamecenter/laz-games-terms-conditions?hybrid=1",
    "Grab": "https://www.grab.com/vn/en/brand%20%20story/",
    "Innovature BPO": "https://innovaturebpo.com/",
    "Amanotes": "https://www.careers.amanotes.com/",
    "Bosch Vietnam": "https://www.bosch.com.vn/contact/",
    "Intel Products Vietnam": "https://www.intel.com/content/www/us/en/corporate-responsibility/community-global-sites.html",
    "Renesas Vietnam": "https://www.renesas.com/en/about/newsroom/renesas-electronics-semiconductor-design-subsidiary-vietnam-receives-excellence-award-ministry",
    "Synopsys Vietnam": "https://www.synopsys.com/company/contact-synopsys/office-locations/south-asia.html",
    "Marvell Vietnam": "https://www.marvell.com/company/newsroom/marvell-accelerates-expansion-in-vietnam.html",
    "HSBC Vietnam": "https://www.hsbc.com.vn/en-vn/contact/branch-finder/",
    "Shinhan Bank Vietnam": "https://shinhan.com.vn/en/news-media/announcement-on-changing-address-of-head-office-2.html",
    "UOB Vietnam": "https://www.uob.com.vn/web-resources/general/pdf/general/en/common/uob-and-intellect.pdf",
    "FE Credit": "https://fecredit.com.vn/cong-ty-tnhh-mtv-ngan-hang-viet-nam-thinh-vuong-duoc-chap-thuan-nguyen-tac-chuyen-doi-hinh-thuc-phap-ly/",
    "Home Credit Vietnam": "https://career.homecredit.vn/position/head_of_customer_value_management",
    "TARA JSC": "https://tara.com.vn/ve-chung-toi-1",
    "Cloud Ace": "https://cloud-ace.vn/tin-tuc/customer/thanh-lap-chi-nhanh-moi-cua-cloud-ace-vietnam-tai-ha-noi/",
    "ELSA": "https://vn.elsaspeak.com/lien-he/",
    "FPT Online": "https://i.fptonline.net/2019/04/T%C3%A0i-li%E1%BB%87u-%C4%90%E1%BA%A1i-h%E1%BB%99i-c%E1%BB%95-%C4%91%C3%B4ng-th%C6%B0%E1%BB%9Dng-ni%C3%AAn-2019_sign.pdf",
    "Payoo": "https://payoo.vn/lien-he.html",
}

HCMC_NAMES = frozenset(name.casefold() for name in HCMC_BASED)
