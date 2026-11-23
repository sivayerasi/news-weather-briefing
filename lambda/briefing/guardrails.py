"""Input validation and output scrubbing."""
import re

MAX_INTERESTS = 10
CITY_RE = re.compile(r"^[^\W\d_][\w .,'\-]{0,79}$", re.UNICODE)
INTEREST_RE = re.compile(r"^[\w .&'\-]{1,40}$", re.UNICODE)
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
VALID_FAIL_SOURCES = {"weather", "news"}

SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ASIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(aws_)?secret(_access)?_key\s*[:=]\s*\S+"),
    re.compile(r"(?i)(api[_-]?key|token|password)\s*[:=]\s*\S+"),
    re.compile(r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])"),
]


class ValidationError(ValueError):
    pass


def parse_interests(value):
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        raise ValidationError("interests must be a comma-separated list")
    cleaned = [str(v).strip() for v in value if str(v).strip()]
    if not cleaned:
        raise ValidationError("at least one interest is required")
    if len(cleaned) > MAX_INTERESTS:
        raise ValidationError(f"at most {MAX_INTERESTS} interests allowed")
    for item in cleaned:
        if not INTEREST_RE.match(item):
            raise ValidationError(f"invalid interest: {item[:40]!r}")
    return cleaned


def validate_request(body, require_email=False):
    """Return a clean dict {city, interests, email, fail_sources} or raise ValidationError."""
    if not isinstance(body, dict):
        raise ValidationError("request body must be a JSON object")
    city = str(body.get("city", "")).strip()
    if not CITY_RE.match(city):
        raise ValidationError("city is missing or has invalid characters")
    interests = parse_interests(body.get("interests", ""))
    email = str(body.get("email", "")).strip().lower()
    if email or require_email:
        if not EMAIL_RE.match(email):
            raise ValidationError("a valid email is required")
    fail = body.get("simulate_failure") or []
    if isinstance(fail, str):
        fail = [fail]
    if not isinstance(fail, list) or not set(fail) <= VALID_FAIL_SOURCES:
        raise ValidationError("simulate_failure must be 'weather' and/or 'news'")
    return {"city": city, "interests": interests, "email": email, "fail_sources": tuple(fail)}


def scrub_output(text):
    """Redact anything that looks like a credential before it leaves the system."""
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text
