"""Location rules for the Hanoi-focused job search."""

import re
import unicodedata


def _plain(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(character for character in value if not unicodedata.combining(character))
    return value.replace("đ", "d")


HCMC = re.compile(r"\b(?:ho\s*chi\s*minh|hcmc|hcm|tp\.?\s*hcm|tphcm|sai\s*gon|saigon)\b")
OTHER_LOCATION = re.compile(
    r"\b(?:ha\s*noi|hanoi|remote|tu\s*xa|da\s*nang|danang|hai\s*phong|haiphong|"
    r"can\s*tho|cantho|hue|bac\s*ninh)\b"
)
CITY = re.compile(rf"(?:{HCMC.pattern}|{OTHER_LOCATION.pattern})")
PLACEHOLDERS = {"", "search by location", "tim kiem theo dia diem"}


def job_location(location: str | None, title: str, description: str) -> str:
    """Use the posting header when a career page omits its location field."""
    stated = (location or "").strip()
    if _plain(stated) not in PLACEHOLDERS:
        return stated

    title_match = re.match(r"^\s*[\[(]([^)\]]+)[)\]]", title)
    if title_match and CITY.search(_plain(title_match.group(1))):
        return title_match.group(1).strip()

    lines = [line.strip() for line in description.splitlines() if line.strip()]
    for index, line in enumerate(lines[:16]):
        plain = _plain(line)
        if re.match(r"^(?:location|work location|dia diem(?: lam viec)?)\s*:", plain):
            value = line.split(":", 1)[1].strip()
            if value and CITY.search(_plain(value)):
                return value
            following = [item.lstrip("•- ") for item in lines[index + 1:index + 4]
                         if item.startswith(("•", "-")) and CITY.search(_plain(item))]
            if following:
                return " / ".join(following)
        if line.startswith(("•", "-")) and len(line) <= 180 and CITY.search(plain):
            nearby = [item.lstrip("•- ") for item in lines[index:index + 3]
                      if item.startswith(("•", "-")) and len(item) <= 180 and CITY.search(_plain(item))]
            return " / ".join(nearby)
        if len(line) <= 80 and CITY.fullmatch(plain.strip(" ,.;")):
            return line
    return stated


def is_hcm_only(location: str | None) -> bool:
    """Exclude explicit HCMC locations unless another city or remote work is listed."""
    normalized = _plain(location or "")
    return bool(HCMC.search(normalized)) and not bool(OTHER_LOCATION.search(normalized))
