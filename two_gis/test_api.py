"""Offline tests for the /2gis/* endpoint functions and the catalog call's
error handling: the upstream calls are replaced with the fixtures."""
import gzip
import json
from pathlib import Path

import pytest

import config
import route_glue
from cache import DontCache
from two_gis import api, fetch, parsers, refs
from two_gis.fetch import TwoGisBadRequest, TwoGisBlocked, TwoGisNotFound, TwoGisUpstreamError

FIXTURES = Path(__file__).parent / "fixtures"
RESTAURANT, BAR, SPARSE, UNKNOWN, CHAIN = ("70000001057394703", "70000001034739765", "70000001119403627",
                                           "70000001999999999", "70000001034739764")


REAL_FETCH_PHOTOS, REAL_FETCH_REVIEWS = fetch.fetch_photos, fetch.fetch_reviews     # the fixture below replaces them


def load(name):
    packed = FIXTURES / f"{name}.json.gz"
    if packed.exists():
        return json.loads(gzip.decompress(packed.read_bytes()))
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No network and no cache store: cached_call runs the function, the
    region table comes from the fixture, every other upstream call fails
    unless a test replaces it."""
    def uncached(namespace, data, fn, ttl, **kw):
        result = fn(**data)
        return result.data if isinstance(result, DontCache) else result      # as cache.cached_call hands it back
    monkeypatch.setattr(api, "cached_call", uncached)
    monkeypatch.setattr(fetch, "fetch_regions", lambda locale=None: load("regions_english")["items"])
    for name in ("fetch_search", "fetch_items", "fetch_city", "fetch_suggestions", "fetch_rubrics", "fetch_reviews",
                 "fetch_review_topics", "fetch_review_ratings", "fetch_review_summary", "fetch_photos"):
        def refuse(*args, _name=name, **kwargs):
            raise AssertionError(f"unexpected upstream call {_name}{args}")
        monkeypatch.setattr(fetch, name, refuse)
    api._memo.clear()
    fetch._keys.reset()
    yield
    api._memo.clear()
    fetch._keys.reset()


def search_data(**given):
    base = {"query": "кафе", "city": None, "latitude": None, "longitude": None, "radius": None, "polygon": None,
            "category_id": None, "district_id": None, "metro_id": None, "sort": "relevance", "min_rating": None,
            "opened_after": None, "attributes": None, "language": None, "page": 1, "limit": 3}
    return {**base, **given}


# ---- the city --------------------------------------------------------------------------

def test_city_by_slug_and_name_is_the_whole_region():
    for text in ("moscow", "Moscow", "MOSCOW"):
        city = api._city(text)
        assert city["region"]["id"] == "32" and city["city_id"] is None and city["slug"] == "moscow"
    assert api._city("dubai")["region"]["domain"] == "ae"
    assert api._city("cyprus")["region"]["domain"] == "com.cy"


def test_a_town_inside_a_region_narrows_the_search(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_city", lambda name: load("city_krasnogorsk")["items"])
    city = api._city("Krasnogorsk")
    assert city["region"]["id"] == "32" and city["city_id"] == "4504222397629287" and city["name"] == "Krasnogorsk"
    assert api._city_summary(city) == {"name": "Krasnogorsk", "slug": "moscow", "link": "https://2gis.ru/moscow",
                                       "region_id": "32", "city_id": "4504222397629287", "country_code": "RU"}


def test_a_regions_own_city_by_another_name_is_the_region(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_city", lambda name: [{"id": "13933634117435393_x", "type": "adm_div", "subtype": "city",
                                                           "name": "Dubai", "city_alias": "dubai", "region_id": "99"}])
    city = api._city("Дубай")
    assert city["region"]["id"] == "99" and city["city_id"] is None and city["name"] == "UAE"


def test_unknown_city_is_not_found(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_city", lambda name: load("city_new_york_junk")["items"])
    with pytest.raises(TwoGisNotFound, match="not on 2GIS"):
        api._city("New York")
    monkeypatch.setattr(fetch, "fetch_city", lambda name: [])
    with pytest.raises(TwoGisNotFound):
        api._city("Atlantis")


def test_search_language_must_be_one_of_the_citys_country():
    moscow = api._city("moscow")
    assert api._search_locale("en", moscow) == "en_RU"
    assert api._search_locale(None, moscow) is None
    assert api._search_locale("ar", api._city("dubai")) == "ar_AE"
    with pytest.raises(ValueError, match="language: Moscow is answered in: .*en.*ru"):
        api._search_locale("ar", moscow)
    with pytest.raises(ValueError, match="language: give a city"):
        api._search_locale("en", None)
    # a record asked by id takes any language the site has
    assert api._record_locale("ar") == "ar_AE" and api._record_locale(None) is None


# ---- places/search ---------------------------------------------------------------------

def test_search(monkeypatch):
    sent = {}

    def fake(params):
        sent.update(params)
        return load("search_cafe_moscow")
    monkeypatch.setattr(fetch, "fetch_search", fake)
    out = api.places_search(**search_data(city="moscow", sort="opened_time", min_rating="4.5", has_website=True, wifi=False,
                                          attributes=[["food_service_avg_price", ["300", "600"]]], category_id=["161"]))
    assert sent["region_id"] == "32" and sent["city_id"] is None and sent["q"] == "кафе" and sent["sort"] == "opened_time"
    assert sent["has_site"] == "true" and sent["attr[rating_rating_excellent]"] == "true"
    assert sent["attr[food_service_avg_price]"] == "300,600" and sent["rubric_id"] == "161"
    assert "attr[additionally_services_wifi]" not in sent          # wifi=false is no filter
    assert list(out)[:3] == ["query", "city", "sort"] and out["sort"] == "newest"
    assert out["city"]["slug"] == "moscow" and out["pagination"]["total_count"] == 8597
    assert out["places"][0]["link"].startswith("https://2gis.ru/moscow/firm/")


def test_the_chain_rating_is_on_and_the_legal_name_off(monkeypatch):
    # the owner's decision of 2026-11-23 on the chain rating; none yet on the advertiser's legal name
    assert config.TWO_GIS_CHAIN_RATING is True and config.TWO_GIS_ADVERTISER_LEGAL_NAME is False
    monkeypatch.setattr(fetch, "fetch_items", lambda ids, locale=None: load("place_chain_bar")["items"])
    out = api.places_details(id=BAR, language=None)
    assert out["chain"]["rating"] == 4.7 and out["chain"]["review_count"] > 4000 and out["chain"]["rating_count"] > 5000
    assert "advertiser_legal_name" not in out["promotion"]


def test_search_switches_add_the_values_the_page_does_not_print(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_search", lambda params: load("search_cafe_moscow"))
    monkeypatch.setattr(config, "TWO_GIS_CHAIN_RATING", False)
    assert "rating" not in api.places_search(**search_data(city="moscow"))["places"][0]["chain"]
    monkeypatch.setattr(config, "TWO_GIS_CHAIN_RATING", True)
    assert "rating" in api.places_search(**search_data(city="moscow"))["places"][0]["chain"]


def test_search_pages_stop_at_the_depth_the_site_serves(monkeypatch):
    # the site states 24,203 rows and serves the first 10,000: its next page is a "not found"
    def served(params):
        if (params["page"] - 1) * params["page_size"] >= refs.MAX_SEARCH_ROWS:
            raise TwoGisNotFound("past the end")
        return {"total": 24203, "items": load("search_cafe_moscow")["items"][:1]}
    monkeypatch.setattr(fetch, "fetch_search", served)
    path, host = "/2gis/places/search", "2gis-scraper.omkar.cloud"

    def page(number):
        query = {"query": "restaurant", "city": "Dubai", "limit": "50", "page": str(number)}
        return route_glue.paginate(api.places_search(**search_data(query="restaurant", city="dubai", page=number, limit=50)),
                                   path, query, host)

    last = page(200)
    assert last["count"] == 24203 and last["total_pages"] == 200 and last["places"]
    assert last["next"] is None and "page=199" in last["previous"]
    assert "page=200" in page(199)["next"]
    past = page(201)
    assert past["places"] == [] and past["total_pages"] == 200 and past["next"] is None and "page=200" in past["previous"]


def test_has_prices_falls_back_to_the_other_tag_where_the_market_lacks_the_first(monkeypatch):
    calls = []

    def uae(params):
        calls.append(dict(params))
        if "attr[sorting_has_goods]" in params:
            raise TwoGisNotFound("the UAE has no such tag")
        assert params["has_goods"] == "true"
        return load("search_restaurant_dubai")
    monkeypatch.setattr(fetch, "fetch_search", uae)
    out = api.places_search(**search_data(query="restaurant", city="dubai", has_prices=True))
    assert len(calls) == 2 and "has_goods" not in calls[0] and "attr[sorting_has_goods]" not in calls[1]
    assert out["places"] and out["pagination"]["total_count"] == load("search_restaurant_dubai")["total"]


def test_has_prices_on_a_later_page_of_the_fallback(monkeypatch):
    def uae(params):
        if "attr[sorting_has_goods]" in params:
            raise TwoGisNotFound("the UAE has no such tag")
        return {"total": 707, "items": load("search_restaurant_dubai")["items"]}
    monkeypatch.setattr(fetch, "fetch_search", uae)
    out = api.places_search(**search_data(query="restaurant", city="dubai", has_prices=True, page=3, limit=20))
    assert out["places"] and out["pagination"] == {"page": 3, "items_per_page": 20, "total_pages": 36, "total_count": 707}


def test_has_prices_keeps_the_wider_tag_where_the_market_has_it(monkeypatch):
    calls = []

    def moscow(params):
        calls.append(params)
        return load("search_cafe_moscow")
    monkeypatch.setattr(fetch, "fetch_search", moscow)
    out = api.places_search(**search_data(city="moscow", has_prices=True))
    assert len(calls) == 1 and calls[0]["attr[sorting_has_goods]"] == "true" and "has_goods" not in calls[0]
    assert out["places"]


def test_has_prices_with_nothing_under_either_tag_is_an_empty_page(monkeypatch):
    def nothing(params):
        raise TwoGisNotFound("nothing")
    monkeypatch.setattr(fetch, "fetch_search", nothing)
    out = api.places_search(**search_data(query="zxqvwpfk", city="dubai", has_prices=True))
    assert out["places"] == [] and out["pagination"]["total_count"] == 0


def test_no_other_search_asks_twice_when_it_finds_nothing(monkeypatch):
    calls = []

    def nothing(params):
        calls.append(params)
        raise TwoGisNotFound("nothing")
    monkeypatch.setattr(fetch, "fetch_search", nothing)
    api.places_search(**search_data(query="zxqvwpfk", city="dubai"))
    assert len(calls) == 1


def test_search_with_no_result_and_past_the_end(monkeypatch):
    def nothing(params):
        raise TwoGisNotFound("nothing")
    monkeypatch.setattr(fetch, "fetch_search", nothing)
    out = api.places_search(**search_data(city="moscow", query="zxqvwpfk"))
    assert out["places"] == [] and out["pagination"]["total_count"] == 0

    calls = []

    def past_end(params):
        calls.append((params["page"], params["page_size"]))
        if params["page"] > 1:
            raise TwoGisNotFound("past the end")
        return {"total": 8597, "items": []}
    monkeypatch.setattr(fetch, "fetch_search", past_end)
    out = api.places_search(**search_data(city="moscow", page=5000, limit=20))
    assert calls == [(5000, 20), (1, 1)]
    assert out["places"] == [] and out["pagination"] == {"page": 5000, "items_per_page": 20, "total_pages": 430, "total_count": 8597}


def test_search_refuses_an_attribute_the_site_ignored(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_search", lambda params: load("search_cafe_moscow"))
    with pytest.raises(ValueError, match="attributes: 'bogus_tag_xyz' is not a filter"):
        api.places_search(**search_data(city="moscow", attributes=[["bogus_tag_xyz", None]]))
    # a tag the rows carry passes
    tag = sorted(parsers.feature_tags(load("search_cafe_moscow")["items"][0]))[0]
    assert api.places_search(**search_data(city="moscow", attributes=[[tag, None]]))["places"]


def test_search_accepts_a_tag_that_only_filters(monkeypatch):
    # the answer offers these tags as filters and the rows they select do not carry them (the rows here are
    # rated 4.7 and 4.8 and none carries a rating tag): they are filters of the search, not ignored ones
    result = load("search_cafe_moscow")
    assert not any("rating_rating_pretty_good" in parsers.feature_tags(row) for row in result["items"])
    assert {"rating_rating_pretty_good", "work_round_the_clock"} <= parsers.offered_tags(result)
    monkeypatch.setattr(fetch, "fetch_search", lambda params: result)
    for tag in ("rating_rating_pretty_good", "work_round_the_clock", "sorting_has_goods", "food_service_food_italian"):
        assert api.places_search(**search_data(city="moscow", attributes=[[tag, None]]))["places"]
    # an answer that offers no filters: the tags this package filters by itself are still known
    monkeypatch.setattr(fetch, "fetch_search", lambda params: {**result, "filters": {}})
    assert api.places_search(**search_data(city="moscow", attributes=[["work_round_the_clock", None]]))["places"]
    with pytest.raises(ValueError, match="attributes: 'food_service_food_italian' is not a filter"):
        api.places_search(**search_data(city="moscow", attributes=[["food_service_food_italian", None]]))


def test_search_language_without_city_is_a_400():
    with pytest.raises(ValueError, match="language"):
        api.places_search(**search_data(language="en"))


# ---- details, batch, branches ----------------------------------------------------------

def test_details(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_items", lambda ids, locale=None: load("place_restaurant")["items"])
    out = api.places_details(id=RESTAURANT, language=None)
    assert out["id"] == RESTAURANT and out["link"] == f"https://2gis.ru/moscow/firm/{RESTAURANT}"
    monkeypatch.setattr(fetch, "fetch_items", lambda ids, locale=None: [])
    with pytest.raises(TwoGisNotFound, match=UNKNOWN):
        api.places_details(id=UNKNOWN, language=None)


def test_batch_keeps_the_order_asked_and_names_what_is_missing(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_items", lambda ids, locale=None: load("places_batch_three_one_unknown")["items"])
    out = api.places_batch(ids=[SPARSE, UNKNOWN, RESTAURANT], language=None)
    assert out["requested_count"] == 3 and out["found_count"] == 2 and out["not_found"] == [UNKNOWN]
    assert [p["id"] for p in out["places"]] == [SPARSE, RESTAURANT]


def test_branches_of_a_places_chain(monkeypatch):
    sent = {}
    monkeypatch.setattr(fetch, "fetch_items", lambda ids, locale=None: load("place_chain_bar")["items"])

    def fake(params):
        sent.update(params)
        return load("chain_branches")
    monkeypatch.setattr(fetch, "fetch_search", fake)
    out = api.places_branches(id={"place": BAR}, city=None, language=None, page=1, limit=50)
    assert sent["org_id"] == CHAIN and sent["region_id"] == "32" and sent["q"] is None
    assert out["chain"]["id"] == CHAIN and out["chain"]["branch_count"] == 9
    assert out["city"]["slug"] == "moscow" and out["pagination"]["total_count"] == 9 and len(out["places"]) == 9
    assert "available_filters" not in out


def test_branches_from_a_chain_link_and_not_found(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_search", lambda params: load("chain_branches"))
    out = api.places_branches(id={"chain": CHAIN, "city": "moscow"}, city=None, language=None, page=1, limit=50)
    assert out["chain"]["id"] == CHAIN

    monkeypatch.setattr(fetch, "fetch_items", lambda ids, locale=None: [])
    with pytest.raises(TwoGisNotFound, match="give `city`"):
        api.places_branches(id={"place": UNKNOWN}, city=None, language=None, page=1, limit=20)

    def nothing(params):
        raise TwoGisNotFound("nothing")
    monkeypatch.setattr(fetch, "fetch_search", nothing)
    with pytest.raises(TwoGisNotFound, match="has no branches in UAE"):
        api.places_branches(id={"chain": CHAIN, "city": "moscow"}, city="dubai", language=None, page=1, limit=20)


# ---- reviews, photos -------------------------------------------------------------------

REVIEW_PARAMS = {"sort": "date_created", "rating": None, "source": None, "with_answer": None, "without_text": None,
                 "topic": None, "unconfirmed": None, "language": None}


def test_reviews_first_page_carries_the_extras(monkeypatch):
    sent = {}

    def fake(place, limit, offset, sort, **kw):
        sent.update(place=place, limit=limit, offset=offset, sort=sort, **kw)
        return load("reviews_page_1")
    monkeypatch.setattr(fetch, "fetch_reviews", fake)
    monkeypatch.setattr(fetch, "fetch_review_topics", lambda place, locale=None: load("review_topics"))
    monkeypatch.setattr(fetch, "fetch_review_ratings", lambda place: load("review_ratings"))
    monkeypatch.setattr(fetch, "fetch_review_summary", lambda place, locale=None: load("review_summary"))
    out = api.places_reviews(id=BAR, page=1, limit=10, **REVIEW_PARAMS)
    assert sent["offset"] == 0 and sent["confirmed"] is True and sent["sort"] == "date_created"
    assert out["place_id"] == BAR and out["sort"] == "newest" and out["rating"] == 4.8
    assert out["topics"][0]["name"] == "музыка" and out["rating_counts"][0] == {"rating": 5, "count": 1274}
    assert out["summary"].startswith("Этот ресторан") and len(out["reviews"]) == 10
    assert out["pagination"]["total_count"] == 1199


def test_reviews_later_pages_and_a_failing_extra(monkeypatch):
    sent = {}

    def fake(place, limit, offset, sort, **kw):
        sent.update(offset=offset, **kw)
        return load("reviews_last_page")
    monkeypatch.setattr(fetch, "fetch_reviews", fake)
    out = api.places_reviews(id=BAR, page=120, limit=10, **{**REVIEW_PARAMS, "unconfirmed": True, "rating": ["1", "2"]})
    assert sent["offset"] == 1190 and sent["confirmed"] is False and sent["ratings"] == ["1", "2"]
    assert out["topics"] is None and out["rating_counts"] is None and out["summary"] is None      # page 1 only

    def broken(*args, **kwargs):
        raise TwoGisUpstreamError("down")
    monkeypatch.setattr(fetch, "fetch_review_topics", broken)
    monkeypatch.setattr(fetch, "fetch_review_ratings", lambda place: load("review_ratings"))
    monkeypatch.setattr(fetch, "fetch_review_summary", broken)
    out = api.places_reviews(id=BAR, page=1, limit=10, **REVIEW_PARAMS)
    assert out["topics"] is None and out["summary"] is None and out["rating_counts"] and out["reviews"]


def test_reviews_missing_an_unanswered_extra_are_not_kept(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_reviews", lambda *a, **kw: load("reviews_page_1"))
    monkeypatch.setattr(fetch, "fetch_review_topics", lambda place, locale=None: load("review_topics"))
    monkeypatch.setattr(fetch, "fetch_review_ratings", lambda place: load("review_ratings"))

    def down(*args, **kwargs):
        raise TwoGisBlocked("HTTP 429")
    monkeypatch.setattr(fetch, "fetch_review_summary", down)
    out = api._places_reviews(id=BAR, page=1, limit=10, **REVIEW_PARAMS)
    assert isinstance(out, DontCache) and out.data["summary"] is None and out.data["topics"] and out.data["reviews"]

    # a place the site has no summary for is a whole answer, and so is a later page (it asks for no extras)
    def none(*args, **kwargs):
        raise TwoGisNotFound("no summary")
    monkeypatch.setattr(fetch, "fetch_review_summary", none)
    out = api._places_reviews(id=BAR, page=1, limit=10, **REVIEW_PARAMS)
    assert isinstance(out, dict) and out["summary"] is None and out["topics"]
    monkeypatch.setattr(fetch, "fetch_review_summary", down)
    assert isinstance(api._places_reviews(id=BAR, page=2, limit=10, **REVIEW_PARAMS), dict)


def _reviews_answer(monkeypatch, **given):
    """The answer for a place in the market of the fixture (it offers flamp and tbank, not 4sq)."""
    monkeypatch.setattr(fetch, "fetch_reviews", lambda *a, **kw: load("reviews_page_1"))
    monkeypatch.setattr(fetch, "fetch_review_topics", lambda place, locale=None: load("review_topics"))
    monkeypatch.setattr(fetch, "fetch_review_ratings", lambda place: load("review_ratings"))
    monkeypatch.setattr(fetch, "fetch_review_summary", lambda place, locale=None: load("review_summary"))
    return api.places_reviews(id=BAR, page=1, limit=10, **{**REVIEW_PARAMS, **given})


def test_a_source_the_market_lacks_answers_no_reviews(monkeypatch):
    out = _reviews_answer(monkeypatch, source=["4sq"])
    assert "4sq" not in out["sources"]
    assert out["reviews"] == [] and out["pagination"]["total_count"] == 0 and out["pagination"]["total_pages"] == 0
    # what the place itself is stays in the answer
    assert out["rating"] == 4.8 and out["review_count"] == 1253 and out["topics"] and out["rating_counts"]


def test_a_source_the_market_has_keeps_its_reviews(monkeypatch):
    out = _reviews_answer(monkeypatch, source=["tbank"])
    assert len(out["reviews"]) == 10 and out["pagination"]["total_count"] == 1199


def test_a_list_with_one_source_the_market_has_keeps_its_reviews(monkeypatch):
    # the reviews API answers those it knows when the list names at least one of them
    out = _reviews_answer(monkeypatch, source=["4sq", "tbank"])
    assert len(out["reviews"]) == 10


def test_reviews_of_an_unknown_place_are_a_404(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_reviews", lambda *a, **kw: load("reviews_unknown_place"))
    with pytest.raises(TwoGisNotFound, match=UNKNOWN):
        api.places_reviews(id=UNKNOWN, page=1, limit=20, **REVIEW_PARAMS)


def test_photos(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_photos", lambda place, album, limit, cursor, locale: load("photos_page_1"))
    out = api.places_photos(id=RESTAURANT, album="all", limit=8, cursor=None, language=None)
    assert list(out) == ["place_id", "link", "album", "next_cursor", "photos", "albums"]
    assert len(out["photos"]) == 8 and out["next_cursor"] and out["albums"][0]["id"] == "all"

    def rejected(*args):
        raise TwoGisBadRequest("RawMedia.page_token")
    monkeypatch.setattr(fetch, "fetch_photos", rejected)
    with pytest.raises(TwoGisBadRequest, match="^cursor: "):
        api.places_photos(id=RESTAURANT, album="all", limit=8, cursor="a.b.c", language=None)


# ---- suggestions, cities, categories ---------------------------------------------------

def test_suggestions_cities_categories(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_suggestions", lambda query, region_id, locale=None: load("suggestions_coffee")["items"])
    out = api.places_suggestions(query="кофе", city="moscow", language=None)
    assert out["city"]["region_id"] == "32" and len(out["suggestions"]) == 10

    cities = api.cities_list(language="en", country=None)
    assert cities["count"] == 208 and len(cities["cities"]) == 208
    only = api.cities_list(language="en", country="AE")
    assert only["count"] == 1 and only["cities"][0]["slug"] == "dubai"
    assert api.cities_list(language="en", country="US") == {"count": 0, "cities": []}

    monkeypatch.setattr(fetch, "fetch_rubrics", lambda region_id, locale=None: load("categories_moscow")["items"])
    tree = api.categories_list(city="moscow", language=None)
    assert tree["count"] == 1669 and tree["city"]["slug"] == "moscow" and len(tree["categories"]) == 28


# ---- the catalog call's outcomes -------------------------------------------------------

class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body) if not isinstance(body, str) else body

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not JSON")
        return self._body


def _answers(monkeypatch, *responses, reset=True):
    """fetch._send answers these in turn; returns the links it was sent.
    `reset` puts the key pool back on its seeded keys first."""
    queue, links = list(responses), []

    def send(url, headers, label, timeout=None):
        links.append(url)
        return queue.pop(0)
    monkeypatch.setattr(fetch, "_send", send)
    monkeypatch.setattr(config, "MAX_RETRIES", 1)
    if reset:
        fetch._keys.reset()
    return links


def test_catalog_outcomes(monkeypatch):
    links = _answers(monkeypatch, _Resp(200, {"meta": {"code": 200}, "result": {"items": [1], "total": 1}}))
    assert fetch._catalog("/3.0/items", {"q": "кафе"}, "search") == {"items": [1], "total": 1}
    assert "&r=" in links[0] and f"key={config.TWO_GIS_CATALOG_KEY}" in links[0]

    _answers(monkeypatch, _Resp(200, load("search_no_result")))
    with pytest.raises(TwoGisNotFound):
        fetch._catalog("/3.0/items", {"q": "zxqvwpfk"}, "search")

    _answers(monkeypatch, _Resp(200, load("search_bad_param")))
    with pytest.raises(TwoGisBadRequest, match="Param 'sort' is invalid"):
        fetch._catalog("/3.0/items", {"q": "кафе", "sort": "bogus"}, "search")

    _answers(monkeypatch, _Resp(504, "<html>504 Gateway Time-out</html>"))
    with pytest.raises(TwoGisUpstreamError):
        fetch._catalog("/3.0/items", {}, "search")

    _answers(monkeypatch, _Resp(200, "<html>a page</html>"))
    with pytest.raises(TwoGisBlocked):
        fetch._catalog("/3.0/items", {"q": "кафе"}, "search")

    _answers(monkeypatch, _Resp(429, {}))
    with pytest.raises(TwoGisBlocked):
        fetch._catalog("/3.0/items", {"q": "кафе"}, "search")


def test_a_refused_key_is_read_again_from_the_site_once(monkeypatch):
    refused = _Resp(200, {"meta": {"code": 403, "error": {"type": "apiKeyIsBlocked", "message": "key is blocked"}}})
    page = _Resp(200, '{"webApiKey":"new-catalog-key","reviewApiKey":"new-reviews-key","photoApiKey":"newphoto"}'
                      '<script src="https://d-assets.2gis.ru/app.abcdef0123.js"></script>')
    page.text = page._body
    script = _Resp(200, 'this.KEY=t.webApiKey,this.a="feedfacecafe";')
    script.text = script._body
    good = _Resp(200, {"meta": {"code": 200}, "result": {"total": 5}})
    links = _answers(monkeypatch, refused, page, script, good)
    assert fetch._catalog("/3.0/items", {"q": "кафе"}, "search") == {"total": 5}
    assert links[1] == "https://2gis.ru/" and links[2] == "https://d-assets.2gis.ru/app.abcdef0123.js"
    assert "key=new-catalog-key" in links[3]
    assert links[3].endswith(f"&r={fetch.signature('/3.0/items', {'q': 'кафе', 'key': 'new-catalog-key'}, 'feedfacecafe')}")

    # refused again right away: the same keys come back (no second read of the site) and the call fails as a block
    links = _answers(monkeypatch, refused, refused, reset=False)
    with pytest.raises(TwoGisBlocked, match="refused the key"):
        fetch._catalog("/3.0/items", {"q": "кафе"}, "search")
    assert len(links) == 2 and all("key=new-catalog-key" in link for link in links)


def test_keyed_api_outcomes(monkeypatch):
    _answers(monkeypatch, _Resp(404, {"code": 404, "message": "Объект не найден"}))
    with pytest.raises(TwoGisNotFound, match="place 7 not found"):
        REAL_FETCH_PHOTOS("7")
    _answers(monkeypatch, _Resp(400, {"code": 400, "message": "number must be at most 50"}))
    with pytest.raises(TwoGisBadRequest, match="at most 50"):
        REAL_FETCH_REVIEWS("7", 50, 0, "date_created")
    links = _answers(monkeypatch, _Resp(200, load("reviews_page_1")))
    REAL_FETCH_REVIEWS(BAR, 80, 50, "trust", ratings=["1", "2"], sources=["flamp"], with_answer=True, confirmed=False)
    assert "limit=50" in links[0] and "offset=50" in links[0] and "ratings=1,2" in links[0] and "providers=flamp" in links[0]
    assert "rated=false" in links[0] and "with_answer=true" in links[0] and "without_text" not in links[0]
    assert f"key={config.TWO_GIS_REVIEWS_KEY}" in links[0]
