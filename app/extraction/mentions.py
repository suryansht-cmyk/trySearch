"""Word-boundary alias matching over answer text."""

from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse
import re

from app.crawler.fetch import normalise_site_host
from app.utils import normalise_domain

def evidence_url(value):
    value = (value or '').strip()
    parsed = urlparse(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        return None
    return parsed._replace(fragment='').geturl()[:2048]

def domain_matches(candidate_url, tracked_domain):
    candidate_domain = normalise_domain(candidate_url)
    tracked = normalise_site_host(tracked_domain)
    return bool(candidate_domain and (
        normalise_site_host(candidate_domain) == tracked or
        normalise_site_host(candidate_domain).endswith('.' + tracked)
    ))

def text_mentions_alias(text_value, aliases):
    text_value = text_value or ''
    for alias in aliases:
        alias = (alias or '').strip()
        if len(alias) < 2:
            continue
        if re.search(rf'(?<![\w]){re.escape(alias)}(?![\w])', text_value, flags=re.I):
            return True
    return False

def project_brand_aliases(project):
    domain_label = project['domain'].split('.')[0].replace('-', ' ')
    return list(dict.fromkeys([project['brand_name'], project['domain'], domain_label]))
