"""The /2gis/* endpoint functions (route_glue's route_table calls them with
the schema's loaded kwargs).

Caching (cache.cached_call), keyed on the validated params:

  2gis.regions   the region table (cities/list is a view of it): every
                 endpoint reads it for a place's country site (its link)
                 and a city's languages; kept in the process for an hour
  2gis.city      a city name -> its region and city id
  the answers of places/reviews, places/photos, places/suggestions,
  cities/list and categories/list, under their own names

places/search, places/details, places/batch and places/branches carry a
`website`, so their routes are wrapped with the website-contacts option
(routes.py), which caches each answer under `2gis.<function name>`: these
four functions do not cache themselves.

How a `city` is read: the slug or the name of a region of the site (moscow,
Moscow, Москва, dubai) searches that whole region, as the site's own
/moscow/search/... pages do. Any other name is asked from the catalog
("Красногорск", "Sharjah", "Питер"); a town inside a region narrows the
search to that town.
"""
import threading

import config
from cache import DontCache, cached_call
from scraper_utils import fanout, memo
from scraper_utils.text import clean
from scraper_utils.values import g
from two_gis import cache_config as ttl
from two_gis import fetch, parsers, refs
from two_gis.fetch import TwoGisBadRequest, TwoGisNotFound, TwoGisUpstreamError

DEFAULT_NAMES_LANGUAGE = "en"       # cities/list names its regions in English unless asked otherwise
_NAME_LANGUAGES = ("en", "ru")      # a city may be given by its English or its Russian name
SUMMARY_LOCALE = "ru_RU"            # the reviews API wants a locale for a summary and answers the same text for any
EXTRAS_WORKERS = 3                  # topics, rating counts and summary beside a first page of reviews

# the attribute tags this package filters by itself (the yes/no filters, the rating floor): the catalog knows
# them in every search, and the rows they select do not carry them
_OWN_FILTER_TAGS = ({param[5:-1] for param, _ in refs.FLAG_FILTERS.values() if param.startswith("attr[")}
                    | set(refs.MIN_RATINGS.values()))

_SORT_NAMES = {token: name for name, token in refs.SORTS.items()}
_REVIEW_SORT_NAMES = {token: name for name, token in refs.REVIEW_SORTS.items()}

_memo, _memo_lock = {}, threading.Lock()


def _options():
    """What the config switches add to a place (values the site's data
    carries and its pages do not print)."""
    return {"chain_rating": config.TWO_GIS_CHAIN_RATING,
            "advertiser_legal_name": config.TWO_GIS_ADVERTISER_LEGAL_NAME}


# ---- regions and cities --------------------------------------------------------------

def _fetch_cities(language):
    return parsers.parse_cities(fetch.fetch_regions(refs.LANGUAGE_LOCALES[language]))


def _cities(language):
    """Every region of the site, named in `language` (cached)."""
    return cached_call("2gis.regions", {"language": language}, _fetch_cities, ttl.CITIES_CACHE)


def _build_region_table():
    by_id, by_slug, by_name = {}, {}, {}
    for language in _NAME_LANGUAGES:
        for city in _cities(language):
            region = by_id.setdefault(city["id"], {
                "id": city["id"], "slug": city["slug"], "name": city["name"], "link": city["link"],
                "domain": (city["site"] or "").replace("2gis.", "", 1) or None,
                "country_code": city["country_code"], "languages": city["languages"]})
            for slug in [city["slug"], *city["other_slugs"]]:
                by_slug.setdefault(slug, region)
            by_name.setdefault(refs.city_key(city["name"]), region)
    return {"id": by_id, "slug": by_slug, "name": by_name}


def _region_table():
    """{id / slug / name: region}. A region is {id, slug, name, link,
    domain, country_code, languages}."""
    return memo.expiring(_memo, _memo_lock, "regions", ttl.REGION_TABLE_MEMO_SECONDS, _build_region_table)


def _domains():
    return {region_id: region["domain"] for region_id, region in _region_table()["id"].items()}


def _lookup_city(name):
    return parsers.parse_city_match(fetch.fetch_city(name)) or {}


def _city(text):
    """The `city` param -> {region, city_id, name, slug}. `city_id` is set
    for a town inside a region, None for the region itself. Unknown ->
    TwoGisNotFound."""
    table = _region_table()
    region = table["slug"].get(text.lower()) or table["name"].get(refs.city_key(text))
    if region:
        return {"region": region, "city_id": None, "name": region["name"], "slug": region["slug"]}
    match = cached_call("2gis.city", {"name": refs.city_key(text)}, _lookup_city, ttl.CITY_LOOKUP_CACHE)
    region = table["id"].get(match.get("region_id")) if match else None
    if not region:
        raise TwoGisNotFound(f"city {text!r} is not on 2GIS; cities/list names the ones it covers")
    is_region_itself = match.get("slug") == region["slug"]
    return {"region": region, "city_id": None if is_region_itself else match["city_id"],
            "name": region["name"] if is_region_itself else match["name"],
            "slug": match.get("slug") or region["slug"]}


def _city_summary(city):
    """How the `city` param was read, for the answer."""
    if not city:
        return None
    region = city["region"]
    return {"name": city["name"], "slug": region["slug"], "link": region["link"], "region_id": region["id"],
            "city_id": city["city_id"], "country_code": region["country_code"]}


def _search_locale(language, city):
    """A search is answered only in a language of the city's own country
    (Moscow: ru, en, zh...; Dubai: en, ar): another one finds nothing, so
    it is refused here. None = the city's own language."""
    if not language:
        return None
    if not city:
        raise ValueError("language: give a city with it (a search is answered in the languages of the city's country).")
    region = city["region"]
    locale = f"{language}_{region['country_code']}"
    if locale not in region["languages"]:
        offered = ", ".join(sorted({code.split("_", 1)[0] for code in region["languages"]}))
        raise ValueError(f"language: {region['name']} is answered in: {offered}.")
    return locale


def _record_locale(language):
    """A record asked by id is answered in any language the site has."""
    return refs.LANGUAGE_LOCALES[language] if language else None


# ---- places/search -------------------------------------------------------------------

def _search_filters(data):
    """{upstream param: value} for the yes/no filters, the rating floor and
    the attribute tags of a search."""
    filters = {}
    for name, (param, value) in refs.FLAG_FILTERS.items():
        if data.get(name):
            filters[param] = value
    if data.get("min_rating"):
        filters[f"attr[{refs.MIN_RATINGS[data['min_rating']]}]"] = "true"
    for tag, span in data.get("attributes") or []:
        filters[f"attr[{tag}]"] = f"{span[0]},{span[1]}" if span else "true"
    return filters


def _check_attributes(result, attributes):
    """The catalog ignores an attribute tag it does not know and answers
    the unfiltered list. A tag it knows is one the answer offers as a
    filter (available_filters.attributes), one of the tags this package
    filters by itself, or one a row carries: a row of a filtered list
    carries the attribute it was filtered by, but not a tag that only
    filters (a rating floor, "open around the clock"). A tag that is none
    of these was ignored: say so."""
    rows = [row for row in (result or {}).get("items") or [] if isinstance(row, dict)]
    if not rows:
        return
    known = parsers.offered_tags(result) | _OWN_FILTER_TAGS
    for tag, _ in attributes or []:
        if tag not in known and not any(tag in parsers.feature_tags(row) for row in rows):
            raise ValueError(f"attributes: {tag!r} is not a filter of this search; "
                             "use a `tag` from `available_filters.attributes` of its answer.")


def _search_total(params):
    """How many places a search selects (asked when a page past the end answered nothing)."""
    try:
        return fetch.fetch_search({**params, "page": 1, "page_size": 1}).get("total")
    except TwoGisNotFound:
        return 0


def _search_page(params, page, limit, attributes=None):
    """One page of the catalog -> parse_search's dict. Nothing found is a
    page with no rows; past the end it still says how many there are."""
    try:
        result = fetch.fetch_search(params)
    except TwoGisNotFound:
        result = {"total": _search_total(params) if page > 1 else 0}
    _check_attributes(result, attributes)
    return parsers.parse_search(result, page, limit, _domains(), **_options())


def _with_other_price_tag(params):
    """A search's params with the site's other "with prices" tag (refs.PRICE_FALLBACK)."""
    tag, _ = refs.FLAG_FILTERS["has_prices"]
    name, value = refs.PRICE_FALLBACK
    return {**{key: sent for key, sent in params.items() if key != tag}, name: value}


def places_search(**data):
    city = _city(data["city"]) if data.get("city") else None
    locale = _search_locale(data.get("language"), city)
    point = (data["longitude"], data["latitude"]) if data.get("latitude") is not None else None
    sort = data.get("sort") or "relevance"
    params = fetch.search_params(
        query=data.get("query"),
        region_id=city["region"]["id"] if city else None,
        city_id=city["city_id"] if city else None,
        locale=locale,
        page=data["page"],
        page_size=data["limit"],
        sort=sort,
        point=point,
        radius=data.get("radius") if point else None,
        polygon=data.get("polygon"),
        category_ids=data.get("category_id"),
        district_ids=data.get("district_id"),
        metro_ids=data.get("metro_id"),
        opened_after=data.get("opened_after"),
        filters=_search_filters(data),
    )
    listing = _search_page(params, data["page"], data["limit"], data.get("attributes"))
    if data.get("has_prices") and not listing["pagination"]["total_count"]:
        # a market without the wider price tag selects nothing with it: ask with the other one
        listing = _search_page(_with_other_price_tag(params), data["page"], data["limit"], data.get("attributes"))
    return {
        "query": data.get("query"),
        "city": _city_summary(city),
        "sort": _SORT_NAMES.get(sort, sort),
        **listing,
    }


# ---- places/details, places/batch ----------------------------------------------------

def places_details(**data):
    rows = fetch.fetch_items([data["id"]], _record_locale(data.get("language")))
    places = parsers.parse_places(rows, _domains(), **_options())
    if not places:
        raise TwoGisNotFound(f"place {data['id']} not found")
    return places[0]


def places_batch(**data):
    ids = data["ids"]
    rows = fetch.fetch_items(ids, _record_locale(data.get("language")))
    by_id = {place["id"]: place for place in parsers.parse_places(rows, _domains(), **_options())}
    places = [by_id[i] for i in ids if i in by_id]
    return {
        "requested_count": len(ids),
        "found_count": len(places),
        "not_found": [i for i in ids if i not in by_id],
        "places": places,
    }


# ---- places/branches -----------------------------------------------------------------

def places_branches(**data):
    ref, page, limit = data["id"], data["page"], data["limit"]
    table = _region_table()
    chain_id, region, city_id = ref.get("chain"), None, None
    city_text = data.get("city") or ref.get("city")
    if city_text:
        city = _city(city_text)
        region, city_id = city["region"], city["city_id"]
    if not chain_id:
        rows = fetch.fetch_items([ref["place"]])
        if rows:
            chain_id = clean(g(rows[0], "org", "id", default=None))
            if not chain_id:
                raise TwoGisNotFound(f"{ref['place']} belongs to no chain")
            region = region or table["id"].get(str(rows[0].get("region_id")))
        else:
            chain_id = ref["place"]         # not a place: a chain's own id, which needs a city
    if not region:
        raise TwoGisNotFound(f"place {chain_id} not found; for a chain's own id, give `city` as well")
    params = fetch.search_params(region_id=region["id"], city_id=city_id, page=page, page_size=limit,
                                 locale=_search_locale(data.get("language"), {"region": region}), chain_id=chain_id)
    listing = _search_page(params, page, limit)
    if not listing["pagination"]["total_count"]:
        raise TwoGisNotFound(f"chain {chain_id} has no branches in {region['name']}")
    first = next(iter(listing["places"]), None)
    listing.pop("available_filters", None)
    return {
        "chain": (first or {}).get("chain") or {"id": chain_id},
        "city": {"name": region["name"], "slug": region["slug"], "link": region["link"], "region_id": region["id"],
                 "city_id": city_id, "country_code": region["country_code"]},
        **listing,
    }


# ---- places/reviews ------------------------------------------------------------------

def _review_extras(place, locale):
    """What the reviews tab shows above the list: the topics the reviews
    mention, how many gave each rating and the site's own summary. Each is
    one small call; one that fails is answered as null. -> (the three,
    whether all were answered): a call the site did not answer (a block, a
    5xx, a timeout) may pass on the next try, so that answer is not kept."""
    unanswered = []

    def noting(fn):
        def run():
            try:
                return fn()
            except TwoGisUpstreamError:
                unanswered.append(fn)
                raise
        return run
    soft = fanout.gather(lambda fns: fanout.per_call(fns, EXTRAS_WORKERS), {
        "topics": noting(lambda: parsers.parse_review_topics(fetch.fetch_review_topics(place, locale))),
        "rating_counts": noting(lambda: parsers.parse_rating_counts(fetch.fetch_review_ratings(place))),
        "summary": noting(lambda: parsers.parse_review_summary(fetch.fetch_review_summary(place, locale or SUMMARY_LOCALE))),
    }, (TwoGisUpstreamError, TwoGisNotFound, TwoGisBadRequest))
    return soft, not unanswered


def _places_reviews(**data):
    place, page, limit = data["id"], data["page"], data["limit"]
    locale = _record_locale(data.get("language"))
    sort = data.get("sort") or refs.REVIEW_SORTS["newest"]
    payload = fetch.fetch_reviews(
        place, limit, (page - 1) * limit, sort,
        ratings=data.get("rating"), sources=data.get("source"), with_answer=bool(data.get("with_answer")),
        without_text=bool(data.get("without_text")), topic=data.get("topic"),
        confirmed=not data.get("unconfirmed"), locale=locale)
    if not parsers.is_known_place(payload):
        raise TwoGisNotFound(f"place {place} not found")
    asked = data.get("source")
    if asked and not set(asked) & set(parsers.review_sources(payload)):
        # the reviews API drops a filter that names only sources this market lacks and answers every review
        payload = parsers.without_reviews(payload)
    parsed = parsers.parse_reviews(payload, page, limit)
    extras, complete = _review_extras(place, locale) if page == 1 else ({}, True)
    answer = {
        "place_id": place,
        "link": refs.place_link(place) + "/tab/reviews",
        "rating": parsed["rating"],
        "is_rating_confirmed": parsed["is_rating_confirmed"],
        "review_count": parsed["review_count"],
        "sources": parsed["sources"],
        "summary": extras.get("summary"),
        "topics": extras.get("topics"),
        "rating_counts": extras.get("rating_counts"),
        "sort": _REVIEW_SORT_NAMES.get(sort, sort),
        "pagination": parsed["pagination"],
        "reviews": parsed["reviews"],
    }
    # a first page missing what an unanswered call adds is served but not kept, so the next call asks again
    return answer if complete else DontCache(answer)


def places_reviews(**data):
    return cached_call("2gis.reviews", data, _places_reviews, ttl.REVIEWS_CACHE)


# ---- places/photos -------------------------------------------------------------------

def _places_photos(**data):
    place, album = data["id"], data.get("album") or "all"
    try:
        payload = fetch.fetch_photos(place, album, data["limit"], data.get("cursor"),
                                     _record_locale(data.get("language")))
    except TwoGisBadRequest:
        if data.get("cursor"):
            raise TwoGisBadRequest("cursor: not a `next_cursor` this endpoint returned (one is valid for seven days).")
        raise
    parsed = parsers.parse_photos(payload)
    return {
        "place_id": place,
        "link": refs.place_link(place) + "/tab/photos",
        "album": album,
        "next_cursor": parsed["next_cursor"],
        "photos": parsed["photos"],
        "albums": parsed["albums"],
    }


def places_photos(**data):
    return cached_call("2gis.photos", data, _places_photos, ttl.PHOTOS_CACHE)


# ---- places/suggestions --------------------------------------------------------------

def _places_suggestions(**data):
    city = _city(data["city"])
    region = city["region"]
    rows = fetch.fetch_suggestions(data["query"], region["id"], _search_locale(data.get("language"), city))
    return {
        "query": data["query"],
        "city": _city_summary(city),
        "suggestions": parsers.parse_suggestions(rows, region["domain"]),
    }


def places_suggestions(**data):
    return cached_call("2gis.suggestions", data, _places_suggestions, ttl.SUGGESTIONS_CACHE)


# ---- cities/list, categories/list ----------------------------------------------------

def cities_list(**data):
    cities = _cities(data.get("language") or DEFAULT_NAMES_LANGUAGE)
    if data.get("country"):
        cities = [city for city in cities if city["country_code"] == data["country"]]
    return {"count": len(cities), "cities": cities}


def _categories_list(**data):
    city = _city(data["city"])
    region = city["region"]
    tree = parsers.parse_categories_tree(fetch.fetch_rubrics(region["id"], _search_locale(data.get("language"), city)),
                                         region["domain"], region["slug"])
    return {
        "city": _city_summary({**city, "city_id": None}),
        "count": sum(len(group["subcategories"]) for group in tree),
        "categories": tree,
    }


def categories_list(**data):
    return cached_call("2gis.categories", data, _categories_list, ttl.CATEGORIES_CACHE)
