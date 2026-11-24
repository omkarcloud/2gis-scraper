"""Reference resolvers, link builders and the choice tables for /2gis/*.

ONE param per input: `id` accepts a place id OR any 2GIS place link; `ids`
up to 100 of those; the branches endpoint's `id` also accepts a chain id or
a "all branches" link. Resolvers raise ValueError with a user-facing
message (RefField turns it into a 400).

Identifier shapes (2gis.ru / 2gis.ae / 2gis.kz, 2026-11-21):
  * place   digits only, 13 to 20 of them (70000001057394703,
            4504127908528010). A search answer writes it as <id>_<hash>:
            the part before the underscore is the id. Links:
            https://2gis.ru/moscow/firm/<id>, .../firm/<id>/tab/reviews,
            .../firm/<id>/37.64,55.74 and https://2gis.com/firm/<id>
            (which the site redirects to the place's own country and city).
            A building, a station or a street has the same kind of id and
            the link .../geo/<id>.
  * chain   the organisation a place belongs to: the same kind of id, one
            less than its first place's as a rule (70000001034739764). Its
            link is https://2gis.ru/moscow/branches/<id>.
  * city    a region's slug (moscow, dubai, almaty: the first path segment
            of every link) or a city name in any language the site knows.
"""
import re
from urllib.parse import quote, unquote, urlparse

SITE = "https://2gis.ru"
ANY_HOST = "https://2gis.com"        # /firm/<id> here redirects to the place's own country site and city
_HOST_RE = re.compile(r"(^|\.)2gis\.(com\.cy|[a-z]{2,3})$")
SHORT_LINK_HOST = "go.2gis.com"

ID_RE = re.compile(r"^[0-9]{6,20}$")
_SLUG_RE = re.compile(r"^[a-z0-9_-]{2,40}$")

MAX_BATCH = 100             # "Length of parameter 'id' should be from 1 to 100" (2026-11-21)
MAX_CITY_LENGTH = 80
# How deep the catalog serves one search, whatever total it states: 24,203 restaurants in the UAE answer rows 1 to
# 10,000 at page sizes 3, 7, 12, 16, 20, 30 and 50, and the next page is empty; a category list and a circle stop at
# the same row (2026-11-23).
MAX_SEARCH_ROWS = 10000

_PLACE_HELP = ("Must be a 2GIS place id (70000001057394703) or a 2GIS place link "
               "(https://2gis.ru/moscow/firm/70000001057394703).")
_CHAIN_HELP = ("Must be a 2GIS place id or place link (its chain is used), or a chain's \"all branches\" link "
               "(https://2gis.ru/moscow/branches/70000001034739764).")
_SHORT_HELP = "A go.2gis.com short link is not read: open it and pass the link it leads to."


def _link_path(value):
    """The path of a 2GIS link (None when the value is not a link). A link
    to another site, or a short link, raises ValueError."""
    if not value.startswith(("http://", "https://", "//", "2gis.", "www.2gis.", "go.2gis.")):
        return None
    url = value
    if url.startswith("//"):
        url = "https:" + url
    elif not url.startswith("http"):
        url = "https://" + url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host == SHORT_LINK_HOST:
        raise ValueError(_SHORT_HELP)
    if not _HOST_RE.search(host):
        raise ValueError("Must be a 2GIS link (2gis.ru, 2gis.ae, 2gis.kz, ...).")
    return unquote(parsed.path or "/")


def _bare_id(value):
    """'70000001057394703' or the search form '70000001057394703_<hash>' -> the id, else None."""
    head = value.split("_", 1)[0]
    return head if ID_RE.match(head) else None


def resolve_place(value):
    """Place id or place link -> the id."""
    value = (value or "").strip()
    path = _link_path(value)
    if path is not None:
        match = re.search(r"/(?:firm|geo)/([0-9]{6,20})(?:[/_]|$)", path)
        if match:
            return match.group(1)
        raise ValueError(_PLACE_HELP)
    found = _bare_id(value)
    if found:
        return found
    raise ValueError(_PLACE_HELP)


def resolve_places(value):
    """'<id>, <link>, <id>' -> the ids in the order given, repeats dropped.
    At most MAX_BATCH (the site's own cap)."""
    ids = []
    for raw in str(value or "").split(","):
        item = raw.strip()
        if not item:
            continue
        found = resolve_place(item)
        if found not in ids:
            ids.append(found)
    if not ids:
        raise ValueError(_PLACE_HELP)
    if len(ids) > MAX_BATCH:
        raise ValueError(f"At most {MAX_BATCH} places per call.")
    return ids


def resolve_chain(value):
    """Place id, place link or a chain's "all branches" link ->
    {"place": id} (the place whose chain is meant, or a chain id given
    bare: api.py tells them apart) or {"chain": id, "city": slug} for a
    branches link, whose first path segment is the city."""
    value = (value or "").strip()
    path = _link_path(value)
    if path is not None:
        match = re.search(r"^/(?:([a-z0-9_-]{2,40})/)?branches/([0-9]{6,20})(?:[/_]|$)", path)
        if match:
            return {"chain": match.group(2), "city": match.group(1)}
        match = re.search(r"/firm/([0-9]{6,20})(?:[/_]|$)", path)
        if match:
            return {"place": match.group(1)}
        raise ValueError(_CHAIN_HELP)
    found = _bare_id(value)
    if found:
        return {"place": found}
    raise ValueError(_CHAIN_HELP)


def resolve_city(value):
    """A city name or slug, or a 2GIS link (its first path segment is the
    city) -> the text api.py looks up."""
    value = " ".join((value or "").split())
    path = _link_path(value)
    if path is not None:
        slug = path.strip("/").split("/", 1)[0]
        if _SLUG_RE.match(slug) and slug not in ("firm", "geo", "branches", "search"):
            return slug
        raise ValueError("Must be a city name (Moscow, Дубай), a 2GIS city slug (moscow) or a 2GIS link with a city.")
    if not value or len(value) > MAX_CITY_LENGTH:
        raise ValueError(f"Must be a city name or a 2GIS city slug, at most {MAX_CITY_LENGTH} characters.")
    return value


def city_key(value):
    """How a city name is compared: case-folded, ё as е, punctuation as spaces."""
    text = (value or "").casefold().replace("ё", "е")
    return " ".join(re.sub(r"[\s\-_.,']+", " ", text).split())


def is_slug(value):
    return bool(_SLUG_RE.match(value or ""))


# ---- links -------------------------------------------------------------------

def site_host(domain):
    """'ru' / 'ae' / 'com.cy' -> https://2gis.ru ...; unknown -> the host that redirects."""
    return f"https://2gis.{domain}" if domain else ANY_HOST


def place_link(place_id, domain=None, city_slug=None, kind="branch"):
    """The place's page. Without the country site and the city it is the
    2gis.com link, which the site redirects to the right one."""
    if not place_id:
        return None
    segment = "firm" if kind in (None, "branch") else "geo"
    if domain and city_slug:
        return f"{site_host(domain)}/{quote(city_slug, safe='')}/{segment}/{place_id}"
    return f"{site_host(domain)}/{segment}/{place_id}"


def branches_link(chain_id, domain=None, city_slug=None):
    """A chain's "all branches" page in one city. It exists only on the
    city's own country site (2gis.com does not redirect it): None without both."""
    if not (chain_id and domain and city_slug):
        return None
    return f"{site_host(domain)}/{quote(city_slug, safe='')}/branches/{chain_id}"


def city_link(domain, slug):
    return f"{site_host(domain)}/{quote(slug, safe='')}" if domain and slug else None


def category_link(domain, city_slug, slug, category_id):
    """A category's listing in a city: /<city>/search/<its slug>/rubricId/<id>
    (the site redirects the category's name in that place to its slug)."""
    if not (domain and city_slug and slug and category_id):
        return None
    return f"{site_host(domain)}/{quote(city_slug, safe='')}/search/{quote(slug, safe='')}/rubricId/{category_id}"


def user_link(public_id):
    """A reviewer's public profile (the site's own /user/<public id> page)."""
    return f"{SITE}/user/{public_id}" if public_id else None


def target_of_redirect(link):
    """The site wraps an outbound link in its click counter:
    http://link.2gis.ru/1.2/<...>?<the real link>. -> the real link; any
    other link as it is."""
    if not isinstance(link, str) or not link:
        return None
    if re.match(r"^https?://link\.2gis\.[a-z.]+/", link) and "?" in link:
        target = link.split("?", 1)[1]
        return target if target.startswith(("http://", "https://")) else None
    return link


# ---- choice tables ------------------------------------------------------------

# public sort -> the catalog's `sort` (its own list of allowed values, read
# from the error it answers for an unknown one, 2026-11-21: flamp_rating,
# distance_without_ads, relevance, creation_time, distance, rating,
# opened_time, name). The site's pages offer rating, opened_time and name.
SORTS = {
    "relevance": "relevance",
    "rating": "rating",
    "newest": "opened_time",
    "name": "name",
    "distance": "distance",
}

# min_rating -> the site's rating filter tag. The lowest rating each let in
# was measured on the last page of "кафе" in Moscow sorted by rating,
# 2026-11-21: 4.9, 4.5, 4.0, 3.5, 3.0.
MIN_RATINGS = {
    "3": "rating_rating_not_bad",
    "3.5": "rating_rating_nice",
    "4": "rating_rating_pretty_good",
    "4.5": "rating_rating_excellent",
    "4.9": "rating_rating_perfect",
}

# the yes/no filters of a search -> the upstream param each one sends
# ("attr[<tag>]" for an attribute tag; the others are params of their own)
FLAG_FILTERS = {
    "has_website": ("has_site", "true"),
    "has_photos": ("has_photos", "true"),
    "open_now": ("work_time", "now"),
    "open_24h": ("attr[work_round_the_clock]", "true"),
    "delivery": ("attr[covid_homedelivery]", "true"),
    "takeaway": ("attr[food_service_details_food_takeaway]", "true"),
    "card_payment": ("attr[general_payment_type_card]", "true"),
    "wifi": ("attr[additionally_services_wifi]", "true"),
    "has_prices": ("attr[sorting_has_goods]", "true"),
    "new_places": ("attr[created_recently]", "true"),
}

# The site's filter panel has two "with prices" tags. has_prices sends the wider one, attr[sorting_has_goods]: a
# product list or a price list as photos (6,332 cafés in Moscow). A market without that tag selects nothing (0 of
# 24,203 restaurants in the UAE), so an empty answer is asked again with the other tag, products only (3,046 in
# Moscow, 707 in the UAE; 2026-11-23).
PRICE_FALLBACK = ("has_goods", "true")

# a tag as the catalog writes it: most are lower-case with underscores, some carry a hyphen or a capital
# (dental_clinics_service_dentist-surgeon, medical_test_med_analyzes_Children_tests; 2026-11-21)
ATTRIBUTE_RE = re.compile(r"^[A-Za-z0-9_-]{2,120}$")
MAX_ATTRIBUTES = 10
MAX_RADIUS = 50000          # "Length of parameter 'radius' should be from 0 to 50000" (2026-11-21)
DEFAULT_RADIUS = 1000

# public review sort -> the reviews API's sort_by (its allowed values:
# date_created, date_edited, friends, trust; `friends` needs a sign-in and
# equals date_created without one)
REVIEW_SORTS = {
    "newest": "date_created",
    "edited": "date_edited",
    "trusted": "trust",
}

# what `rating` takes besides 1..5 (the reviews API's own words)
REVIEW_RATING_GROUPS = ("positive", "neutral", "negative")

# the review sources the reviews API names (the allowed values of its
# `providers` param, 2026-11-21)
REVIEW_SOURCES = ("2gis", "flamp", "otello", "booking", "netmonet", "tbank", "sbertips", "avito", "4sq")
# the catalog's name for a source where the reviews API has another: a place's `review_sources` says foursquare
REVIEW_SOURCE_ALIASES = {"foursquare": "4sq"}

# the photo albums the photo API names (the allowed values of its album
# segment, 2026-11-21); a place lists the ones it has, with counts
ALBUMS = ("all", "video", "food_and_drinks", "price_list_image", "interior", "outside", "veranda", "atmosphere",
          "pool", "services", "goods", "inventory", "information", "kidszone", "staff", "rooms", "view", "area",
          "beach", "spa", "fitness", "entertainment", "animals", "business", "places_nearby", "parking", "lavatory",
          "transport", "relax", "summer", "autumn", "winter", "entrance", "owner", "user")

# language -> a locale the catalog accepts for a record asked by id (any
# country part works there). A SEARCH needs the locale of the city's own
# country, which api.py takes from the city's region.
LANGUAGE_LOCALES = {
    "ru": "ru_RU", "en": "en_AE", "ar": "ar_AE", "kk": "kk_KZ", "uz": "uz_UZ", "az": "az_AZ", "hy": "hy_AM",
    "ka": "ka_GE", "ky": "ky_KG", "tg": "tg_TJ", "cs": "cs_CZ", "it": "it_IT", "es": "es_CL", "uk": "uk_RU",
    "zh": "zh_RU",
}
LANGUAGES = tuple(LANGUAGE_LOCALES)

DAYS = (("Mon", "monday"), ("Tue", "tuesday"), ("Wed", "wednesday"), ("Thu", "thursday"), ("Fri", "friday"),
        ("Sat", "saturday"), ("Sun", "sunday"))
