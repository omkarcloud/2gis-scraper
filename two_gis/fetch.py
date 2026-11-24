"""2GIS transport: plain curl_cffi with chrome TLS on the JSON APIs the
site's own pages call. No browser, no login, no cookie. One session per
request thread on the exit config.TWO_GIS_EXIT names (the server's own IP
in production); a retryable failure drops the session, so the retry rides a
fresh connection.

Three upstreams (every surface answered on 2026-11-21):

  the catalog   https://catalog.api.2gis.ru   (one host for every country site)
    3.0/items          a search: q and/or rubric_id, inside a region
                       (region_id), a city of it (city_id), a circle
                       (point + radius, at most 50,000 m) or a polygon.
                       page_size is at most 50; a search is served to its
                       10,000th row, whatever total it states. With org_id + region_id it lists a
                       chain's branches in that region.
    3.0/items/byid     up to 100 records by id.
    3.0/suggests       what the search box suggests.
    2.0/region/list    every region (a city with its surroundings, or a
                       small country) the site covers.
    2.0/catalog/rubric/list   a region's category tree.
    Every call carries the site's public key and `r`, a checksum over the
    path and the param values (signature() below; the site computes it in
    its app script). The catalog answers HTTP 200 for everything and puts
    the outcome in meta.code: 200, 400 (a param it rejects, with a message
    that names it), 403 apiKeyIsBlocked (a wrong key or checksum) and 404
    itemNotFound (no such record, or a search with no result). A search
    with neither q nor rubric_id answers HTTP 504 after 5 s. The answer
    holds only the fields a call names, so the calls name every field the
    site's own pages ask for.

  the reviews API   https://public-api.reviews.2gis.com   (key in the query, real HTTP statuses)
    3.0/branches/<id>/reviews      50 a page at most, offset to the end;
                                   sort_by, ratings, providers, with_answer,
                                   without_text, facets (`facts`), rated.
                                   An unknown place answers 200 with no
                                   review sources.
                                   `providers` naming only sources the
                                   place's market lacks is dropped: every
                                   review is answered (api.py answers none).
    2.0/objects/branch/<id>/facts  the topics its reviews mention
    2.0/objects/<id>/ratings       how many reviews gave each rating (404 for an unknown place)
    2.0/objects/<id>/summary       the site's own summary of the reviews

  the photo API   https://api.photo.2gis.com
    3.0/objects/<id>/albums/<album>/media   100 a page at most, then a
                                   page token. 404 for an unknown place,
                                   400 for an album or a token it rejects.

The three keys and the checksum salt are public values the site ships to
every visitor: the keys in the page's config, the salt in its app script.
They are held in one shared pool, seeded from config; when the catalog
refuses the key, they are read again from the site (one read per refusal
wave, at most one per config.TWO_GIS_KEY_REFRESH_INTERVAL).

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  TwoGisUpstreamError  transport failure / 5xx / unexpected body   retryable
  TwoGisBlocked        403 / 429 / a refused key / a non-JSON page  retryable
  TwoGisBadRequest     the site rejected the parameters             never retried
  TwoGisNotFound       no such place / nothing found                never retried
"""
import re
import threading
from urllib.parse import quote, urlencode

import config
import egress
import identity_pool
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from scraper_utils import retry, sessions
from two_gis.refs import SITE

IMPERSONATE = "chrome"
API_TIMEOUT = 25
PAGE_TIMEOUT = 30

CATALOG = "https://catalog.api.2gis.ru"
REVIEWS = "https://public-api.reviews.2gis.com"
PHOTOS = "https://api.photo.2gis.com"

MAX_PAGE_SIZE = 50              # the catalog: "Length of parameter 'page_size' should be from 1 to 50"
MAX_IDS = 100                   # the catalog: "Length of parameter 'id' should be from 1 to 100"
MAX_REVIEWS_PAGE = 50           # the reviews API: "number must be at most 50"
MAX_PHOTOS_PAGE = 100           # the photo API: page_size at most 100
REGIONS_PAGE_SIZE = 2000        # what the site's own regions call asks for (208 regions exist)

# What the site's pages send with each kind of call (the session adds its
# own user-agent, client hints, accept-language and accept-encoding).
CATALOG_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "origin": SITE,
    "priority": "u=1, i",
    "referer": SITE + "/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}
# the reviews and photo APIs live on another site than the page
CROSS_SITE_HEADERS = {**CATALOG_HEADERS, "sec-fetch-site": "cross-site"}
PAGE_HEADERS = {
    "accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,image/jxl,image/avif,image/webp,image/apng,"
               "*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"),
    "priority": "u=0, i",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
}
SCRIPT_HEADERS = {
    "accept": "*/*",
    "origin": SITE,
    "referer": SITE + "/",
    "sec-fetch-dest": "script",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "cross-site",
}

# The fields of a record, as the site's own record call names them (its
# getItem list, 2026-11-21), plus the contacts, the brand and the summary
# its pages ask for in separate lists. The catalog returns only what is
# named here.
ITEM_FIELDS = ",".join((
    "items.locale", "items.flags", "items.adm_div", "items.city_alias", "items.region_id", "items.segment_id",
    "items.reviews", "items.point", "items.links", "items.name_ex", "items.name_back", "items.org", "items.group",
    "items.dates", "items.external_content", "items.contact_groups", "items.comment", "items.ads.options",
    "items.email_for_sending.allowed", "items.stat", "items.stop_factors", "items.description",
    "items.geometry.centroid", "items.geometry.selection", "items.geometry.style", "items.timezone_offset",
    "items.context", "items.level_count", "items.address", "items.is_paid", "items.access",
    "items.access_comment", "items.for_trucks", "items.is_incentive", "items.paving_type", "items.capacity",
    "items.schedule", "items.schedule_special", "items.floors", "items.floor_id", "items.floor_plans",
    "items.rubrics", "items.routes", "items.platforms", "items.directions", "items.barrier", "items.reply_rate",
    "items.purpose", "items.purpose_code", "items.attribute_groups", "items.color", "items.route_logo",
    "items.has_goods", "items.has_apartments_info", "items.has_pinned_goods", "items.has_realty",
    "items.has_otello_stories", "items.has_exchange", "items.has_payments", "items.has_dynamic_congestion",
    "items.is_promoted", "items.congestion", "items.delivery", "items.order_with_cart", "items.has_discount",
    "items.metarubrics", "items.detailed_subtype", "items.temporary_unavailable_atm_services",
    "items.poi_category", "items.has_ads_model", "items.vacancies", "items.object_flags",
    "items.structure_info.material", "items.structure_info.floor_type", "items.structure_info.gas_type",
    "items.structure_info.year_of_construction", "items.structure_info.elevators_count",
    "items.structure_info.is_in_emergency_state", "items.structure_info.project_type",
    "items.brand", "items.summary",
))
# a search also names the filters its result offers
SEARCH_FIELDS = ITEM_FIELDS + ",filters"
PLACE_TYPE = "branch"           # a business; the catalog's other kinds are buildings, streets, stations...
CITY_TYPES = "adm_div.city,adm_div.settlement"
CITY_FIELDS = "items.region_id,items.city_alias,items.adm_div,items.point,items.locale,items.full_name"
REGION_FIELDS = ",".join((
    "items.code", "items.uri_code", "items.uri_aliases", "items.name_grammatical_cases", "items.time_zone",
    "items.statistics", "items.domain", "items.locale", "items.locales", "items.satellites", "items.bounds",
    "items.country_code", "items.zoom_level", "items.default_pos", "items.flags",
))
SUGGEST_FIELDS = ",".join((
    "items.alias", "items.name_ex", "items.brand", "items.rubrics", "items.org", "items.adm_div", "items.type",
    "items.subtype", "items.address", "items.segment_id", "items.region_id", "items.locale", "items.context",
    "search_attributes", "items.flags", "items.schedule", "items.tag", "items.reviews", "items.point",
    "items.city_alias",
))
# the business-side kinds of the site's suggestion box: categories, chains, brands, places, filters and phrases
SUGGEST_TYPES = "branch,brand,user_queries,attribute,rubric,meta_rubric,org"
RUBRIC_FIELDS = "items.rubrics,items.rubrics.branch_count,items.branch_count,items.org_count,items.alias,items.caption"
REVIEW_FIELDS = ("meta.providers,meta.branch_rating,meta.branch_reviews_count,meta.total_count,"
                 "reviews.hiding_reason,reviews.emojis,reviews.trust_factors")
PHOTO_PREVIEW_SIZES = "656x340,328x170,232x232,176x176,116x116,88x88"


class TwoGisUpstreamError(UpstreamError):
    """Transport failure, 5xx or an unexpected payload — retryable."""


class TwoGisBlocked(TwoGisUpstreamError, Blocked):
    """403 / 429, a refused key, or a page where JSON was expected — retryable."""


class TwoGisBadRequest(BadRequest):
    """The site rejected the parameters — never retried."""


class TwoGisNotFound(NotFound):
    """No such place, or nothing found — never retried."""


# ---- sessions ------------------------------------------------------------------------

# One curl session per request thread (a curl handle must not be shared
# across threads). The calls are stateless: no cookie is carried.
_local = threading.local()


def _session():
    return sessions.thread_session(
        _local, lambda: egress.exit_session(config.TWO_GIS_EXIT, impersonate=IMPERSONATE))


def _drop_session():
    """Close the thread's session so the retry rides a fresh connection / exit."""
    sessions.drop(_local)


def _retrying(fn):
    """fn() under the shared retry policy: only TwoGisUpstreamError (Blocked
    included) is retried; NotFound and BadRequest surface at once."""
    return retry.run(fn, config, TwoGisUpstreamError, lambda e: _drop_session())


def _send(url, headers, label, timeout=API_TIMEOUT):
    try:
        return _session().get(url, headers=headers, timeout=timeout)
    except Exception as e:
        raise TwoGisUpstreamError(f"request failed for {label}: {type(e).__name__}: {e}")


def _json(resp, label):
    try:
        payload = resp.json()
    except Exception:
        raise TwoGisBlocked(f"non-JSON answer for {label}: HTTP {resp.status_code} {resp.text[:120]!r}")
    if not isinstance(payload, dict):
        raise TwoGisUpstreamError(f"unexpected payload for {label}: {resp.text[:120]!r}")
    return payload


def _link(base, path, params):
    """base + path + the query, values percent-encoded (None values left out)."""
    query = urlencode({k: v for k, v in params.items() if v is not None}, quote_via=quote, safe=",")
    return f"{base}{path}?{query}"


# ---- the keys ------------------------------------------------------------------------

_CONFIG_KEY_RES = {
    "catalog": re.compile(r'"webApiKey":"([^"]{8,80})"'),
    "reviews": re.compile(r'"reviewApiKey":"([^"]{8,80})"'),
    "photos": re.compile(r'"photoApiKey":"([^"]{4,80})"'),
}
_APP_SCRIPT_RE = re.compile(r'src="(https://[^"]+/app\.[0-9a-f]+\.js)"')
_SALT_RE = re.compile(r'\.webApiKey,this\.a="([0-9a-zA-Z]{6,40})"')


def _config_keys():
    return {"catalog": config.TWO_GIS_CATALOG_KEY, "salt": config.TWO_GIS_SIGN_SALT,
            "reviews": config.TWO_GIS_REVIEWS_KEY, "photos": config.TWO_GIS_PHOTOS_KEY}


def parse_keys(page, script):
    """The site's home page and its app script -> {catalog, reviews,
    photos, salt}. Raises TwoGisUpstreamError when one is not there (the
    page's layout changed)."""
    keys = {}
    for name, pattern in _CONFIG_KEY_RES.items():
        match = pattern.search(page or "")
        if not match:
            raise TwoGisUpstreamError(f"the {name} key is not in the site's page config")
        keys[name] = match.group(1)
    match = _SALT_RE.search(script or "")
    if not match:
        raise TwoGisUpstreamError("the checksum salt is not in the site's app script")
    keys["salt"] = match.group(1)
    return keys


def app_script_link(page):
    match = _APP_SCRIPT_RE.search(page or "")
    return match.group(1) if match else None


def _read_keys(key="", hint=None):
    """The pool's mint: the keys as the site ships them right now (two
    requests: its home page, then its app script)."""
    page = _send(SITE + "/", PAGE_HEADERS, "the site's page config", timeout=PAGE_TIMEOUT)
    if page.status_code != 200:
        raise TwoGisBlocked(f"HTTP {page.status_code} for the site's page config")
    script_link = app_script_link(page.text)
    if not script_link:
        raise TwoGisUpstreamError("the app script is not named in the site's page")
    script = _send(script_link, SCRIPT_HEADERS, "the site's app script", timeout=PAGE_TIMEOUT)
    if script.status_code != 200:
        raise TwoGisBlocked(f"HTTP {script.status_code} for the site's app script")
    return identity_pool.Identity(parse_keys(page.text, script.text))


# Shared by every request thread: seeded from config, read again from the
# site when the catalog refuses the key. One read per refusal wave; a
# second refusal inside the interval gets the same keys back and fails as
# a block, so a wrong checksum never turns into a loop of page reads.
_keys = identity_pool.Pool("two-gis-keys", _read_keys,
                           min_interval=lambda: config.TWO_GIS_KEY_REFRESH_INTERVAL,
                           seed={"": _config_keys()})


def _fresh_keys(used):
    """The keys after `used` was refused (the pool reads them once for every thread)."""
    return _keys.get(replace=used)


# ---- the catalog ---------------------------------------------------------------------

def _djb2(text, seed=5381):
    """The site's hash: h = h * 33 + code unit, kept to 32 bits, over UTF-16 code units."""
    h = seed
    data = text.encode("utf-16-le")
    for i in range(0, len(data), 2):
        h = (h * 33 + int.from_bytes(data[i:i + 2], "little")) & 0xFFFFFFFF
    return h


def signature(path, params, salt):
    """The `r` checksum of a catalog call, as the site's app script builds
    it: hash the path followed by every param value (sorted by param name,
    joined with nothing), then hash the salt with that as the seed."""
    values = "".join(str(params[name]) for name in sorted(params))
    return _djb2(salt, _djb2(path + values))


def signed_link(path, params, key, salt):
    """The catalog link for `path` and `params`, with the key and its checksum."""
    sent = {name: value for name, value in params.items() if value is not None}
    sent["key"] = key
    return _link(CATALOG, path, sent) + f"&r={signature(path, sent, salt)}"


def _catalog(path, params, label):
    """One catalog call -> its `result` object. meta.code 404 raises
    TwoGisNotFound, 400 TwoGisBadRequest with the site's message; a refused
    key is read again from the site once, then it is a block."""
    def once():
        ident = _keys.get()
        for attempt in (1, 2):
            keys = ident.value
            resp = _send(signed_link(path, params, keys["catalog"], keys["salt"]), CATALOG_HEADERS, label)
            if resp.status_code in (403, 429):
                raise TwoGisBlocked(f"HTTP {resp.status_code} for {label}")
            if resp.status_code != 200:
                raise TwoGisUpstreamError(f"HTTP {resp.status_code} for {label}")
            payload = _json(resp, label)
            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
            code = meta.get("code")
            error = meta.get("error") if isinstance(meta.get("error"), dict) else {}
            message = str(error.get("message") or "")[:300]
            if code == 200:
                result = payload.get("result")
                return result if isinstance(result, dict) else {}
            if code == 404:
                raise TwoGisNotFound(f"{label}: nothing found")
            if code == 403:
                if attempt == 1:
                    ident = _fresh_keys(ident)
                    continue
                raise TwoGisBlocked(f"the catalog refused the key for {label}: {message}")
            if code == 400:
                raise TwoGisBadRequest(message or f"the site rejected {label}")
            raise TwoGisUpstreamError(f"catalog code {code} for {label}: {message}")
    return _retrying(once)


def search_params(query=None, region_id=None, city_id=None, locale=None, page=1, page_size=MAX_PAGE_SIZE,
                  sort=None, point=None, radius=None, polygon=None, category_ids=None, district_ids=None,
                  metro_ids=None, opened_after=None, filters=None, chain_id=None):
    """The params of a search call. `point` is (lon, lat); `filters` is
    {upstream param: value} for the yes/no filters and attribute tags
    (refs.FLAG_FILTERS, "attr[<tag>]"). `chain_id` lists a chain's branches
    instead of searching."""
    params = {
        "q": query or None,
        "region_id": region_id,
        "city_id": city_id,
        "locale": locale,
        "page": page,
        "page_size": min(page_size, MAX_PAGE_SIZE),
        "fields": SEARCH_FIELDS,
        "type": PLACE_TYPE,
        "sort": sort if sort and sort != "relevance" else None,
        "polygon": polygon,
        "rubric_id": ",".join(category_ids) if category_ids else None,
        "district_id": ",".join(district_ids) if district_ids else None,
        "subway": ",".join(metro_ids) if metro_ids else None,
        "opened_after_date": opened_after,
    }
    if point:
        lon_lat = f"{point[0]},{point[1]}"
        params["point"] = lon_lat
        params["radius"] = radius
        if sort == "distance":
            # what the site sends for "nearest first": the point to measure from
            params["sort_point"] = lon_lat
            params["location"] = lon_lat
    if chain_id:
        params["org_id"] = chain_id
        # the site's own branches call: every branch, not only the ones it ranks as relevant
        params["search_disable_clipping_by_relevance"] = "true"
    params.update(filters or {})
    return params


def fetch_search(params):
    """One result page -> the catalog's result ({items, total, filters, ...}).
    Nothing found (a page past the end included) raises TwoGisNotFound."""
    label = f"search {params.get('q') or params.get('rubric_id') or params.get('org_id') or ''!r}"
    return _catalog("/3.0/items", params, label)


def fetch_items(ids, locale=None):
    """Up to 100 records by id -> the raw records the site knows (unknown
    ids are simply absent; none known -> [])."""
    ids = list(ids)[:MAX_IDS]
    try:
        result = _catalog("/3.0/items/byid", {"id": ",".join(ids), "locale": locale, "fields": ITEM_FIELDS},
                          f"places {','.join(ids)[:60]}")
    except TwoGisNotFound:
        return []
    items = result.get("items")
    return items if isinstance(items, list) else []


def fetch_city(name):
    """The cities and settlements the catalog finds for a name, in any
    language it knows -> its raw rows ([] when it finds none)."""
    try:
        result = _catalog("/3.0/items", {"q": name, "type": CITY_TYPES, "fields": CITY_FIELDS, "page_size": 5},
                          f"city {name!r}")
    except TwoGisNotFound:
        return []
    items = result.get("items")
    return items if isinstance(items, list) else []


def fetch_regions(locale=None):
    """Every region the site covers, named in `locale`'s language (the
    region's own language without one)."""
    result = _catalog("/2.0/region/list", {"locale": locale, "page_size": REGIONS_PAGE_SIZE, "fields": REGION_FIELDS},
                      "regions")
    items = result.get("items")
    if not isinstance(items, list) or not items:
        raise TwoGisUpstreamError(f"regions answered no list: {str(result)[:120]!r}")
    return items


def fetch_suggestions(query, region_id, locale=None):
    """What the search box suggests for a text in a region ([] for none)."""
    try:
        result = _catalog("/3.0/suggests", {"q": query, "region_id": region_id, "locale": locale,
                                            "fields": SUGGEST_FIELDS, "type": SUGGEST_TYPES},
                          f"suggestions {query!r}")
    except TwoGisNotFound:
        return []
    items = result.get("items")
    return items if isinstance(items, list) else []


def fetch_rubrics(region_id, locale=None):
    """A region's category tree: the top-level groups, each with its categories."""
    result = _catalog("/2.0/catalog/rubric/list", {"region_id": region_id, "locale": locale, "fields": RUBRIC_FIELDS},
                      f"categories of region {region_id}")
    items = result.get("items")
    return items if isinstance(items, list) else []


# ---- the reviews and photo APIs ------------------------------------------------------

def _keyed(base, path, params, key_name, label, not_found=None):
    """One call to the reviews or photo API -> the decoded JSON object. A
    refused key (401 / 403) is read again from the site once."""
    def once():
        ident = _keys.get()
        for attempt in (1, 2):
            resp = _send(_link(base, path, {**params, "key": ident.value[key_name]}), CROSS_SITE_HEADERS, label)
            status = resp.status_code
            if status == 200:
                return _json(resp, label)
            if status in (401, 403):
                if attempt == 1:
                    ident = _fresh_keys(ident)
                    continue
                raise TwoGisBlocked(f"HTTP {status} for {label}")
            if status == 404:
                raise TwoGisNotFound(not_found or f"{label} not found")
            if status == 400:
                raise TwoGisBadRequest(_error_message(resp) or f"the site rejected {label}")
            if status == 429:
                raise TwoGisBlocked(f"HTTP 429 for {label}")
            raise TwoGisUpstreamError(f"HTTP {status} for {label}")
    return _retrying(once)


def _error_message(resp):
    """The message of a reviews / photo API error body ({"message": ...})."""
    try:
        return " ".join(str(resp.json().get("message") or "").split())[:300]
    except Exception:
        return ""


def fetch_reviews(place_id, limit, offset, sort, ratings=None, sources=None, with_answer=False,
                  without_text=False, topic=None, confirmed=True, locale=None):
    """One page of a place's reviews with its review totals. `confirmed`
    False lists the reviews the site has not confirmed (they do not count
    in the rating). An unknown place answers no review sources."""
    params = {
        "limit": min(limit, MAX_REVIEWS_PAGE),
        "offset": offset,
        "is_advertiser": "false",
        "fields": REVIEW_FIELDS,
        "rated": "true" if confirmed else "false",
        "sort_by": sort,
        "ratings": ",".join(ratings) if ratings else None,
        "providers": ",".join(sources) if sources else None,
        "with_answer": "true" if with_answer else None,
        "without_text": "true" if without_text else None,
        "facts": topic,
        "locale": locale,
    }
    payload = _keyed(REVIEWS, f"/3.0/branches/{place_id}/reviews", params, "reviews", f"reviews of {place_id}")
    if not isinstance(payload.get("reviews"), list) or not isinstance(payload.get("meta"), dict):
        raise TwoGisUpstreamError(f"unexpected reviews payload: {str(payload)[:160]!r}")
    return payload


def fetch_review_topics(place_id, locale=None):
    """The topics a place's reviews mention ({facts: [...]})."""
    return _keyed(REVIEWS, f"/2.0/objects/branch/{place_id}/facts", {"is_advertiser": "false", "locale": locale},
                  "reviews", f"review topics of {place_id}")


def fetch_review_ratings(place_id):
    """How many reviews gave each rating ({ratings: {"1": n, ...}})."""
    return _keyed(REVIEWS, f"/2.0/objects/{place_id}/ratings", {}, "reviews", f"rating counts of {place_id}")


def fetch_review_summary(place_id, locale=None):
    """The site's own summary of a place's reviews ({text})."""
    return _keyed(REVIEWS, f"/2.0/objects/{place_id}/summary", {"locale": locale}, "reviews",
                  f"review summary of {place_id}")


def fetch_photos(place_id, album="all", page_size=MAX_PHOTOS_PAGE, cursor=None, locale=None):
    """One page of a place's photos and videos with its albums. An unknown
    place raises TwoGisNotFound; a cursor the site rejects, TwoGisBadRequest."""
    params = {"locale": locale, "page_size": min(page_size, MAX_PHOTOS_PAGE), "preview_size": PHOTO_PREVIEW_SIZES,
              "page_token": cursor}
    payload = _keyed(PHOTOS, f"/3.0/objects/{place_id}/albums/{album}/media", params, "photos",
                     f"photos of {place_id}", not_found=f"place {place_id} not found")
    if not isinstance(payload.get("items"), list):
        raise TwoGisUpstreamError(f"unexpected photos payload: {str(payload)[:160]!r}")
    return payload
