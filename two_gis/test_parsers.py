"""Offline tests for the 2GIS parsers, reference resolvers and the request
signing, against the fixtures in two_gis/fixtures/ (the site's own answers,
captured through fetch.py on 2026-11-21 and 2026-11-23)."""
import gzip
import json
from pathlib import Path

import pytest

from two_gis import fetch, parsers, refs

FIXTURES = Path(__file__).parent / "fixtures"
RESTAURANT, BAR, SPARSE, UNKNOWN = "70000001057394703", "70000001034739765", "70000001119403627", "70000001999999999"


def load(name):
    packed = FIXTURES / f"{name}.json.gz"
    if packed.exists():
        return json.loads(gzip.decompress(packed.read_bytes()))
    return json.loads((FIXTURES / f"{name}.json").read_text())


def place(name, **options):
    return parsers.parse_place(load(name)["items"][0], **options)


def walk(value):
    """Every (key, value) pair in a nested answer."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


# ---- a place -------------------------------------------------------------------------

def test_place_restaurant():
    p = place("place_restaurant", domain="ru")
    assert p["id"] == RESTAURANT
    assert p["name"] == "Restaurant by Deep Fried Friends"
    assert p["full_name"] == "Restaurant by Deep Fried Friends, ресторан"
    assert p["name_extension"] == "ресторан"
    assert p["link"] == f"https://2gis.ru/moscow/firm/{RESTAURANT}"
    assert p["type"] == "branch"
    assert p["category"] == "Рестораны"
    assert p["categories"][0] == {"id": "164", "name": "Рестораны", "slug": "restorany", "is_primary": True, "parent_id": "2"}
    assert p["rating"] == 4.9 and p["review_count"] > 100 and p["rating_count"] >= p["review_count"]
    assert p["address"] == "Яузская улица, 10/2 ст4"
    assert p["location"]["street"] == "Яузская улица" and p["location"]["house_number"] == "10/2 ст4"
    assert p["location"]["postcode"] == "109240"
    assert p["location"]["city"] == "Москва" and p["location"]["city_slug"] == "moscow"
    assert p["location"]["district"] == "Таганский" and p["location"]["country"] == "Россия"
    assert p["location"]["latitude"] == 55.746418 and p["location"]["longitude"] == 37.646903
    assert p["location"]["timezone_offset_minutes"] == 180
    assert p["created_at"] == "2021-11-18T00:00:00Z" and p["updated_at"].endswith("Z")
    assert p["has_photos"] is True and p["photo_count"] > 100
    assert p["main_photo"].startswith("https://")


def test_place_contacts_are_clean():
    p = place("place_restaurant")
    assert p["phone"] == "+74959661881"
    assert p["phones"][0] == {"number": "+74959661881", "display": "+7 (495) 966‒18‒81", "comment": None}
    # the site's own website link, not its click counter
    assert p["website"] == "http://www.deepfriedfriends.ru/"
    assert all("link.2gis" not in w["link"] for w in p["websites"])
    kinds = {s["type"]: s["link"] for s in p["social"]}
    assert kinds["telegram"] == "https://t.me/dffradio"
    # the greeting the site pre-fills is dropped from the messenger link
    assert kinds["whatsapp"] == "https://wa.me/74959661881"
    keys = list(p)
    assert keys.index("website") < keys.index("email") < keys.index("phone") < keys.index("social")


def test_website_drops_campaign_tags():
    p = place("place_chain_bar")
    assert p["website"] == "http://tverskaya.rvbar.ru/"
    assert parsers._without_campaign_tags("http://x.ru/?utm_source=2gis&a=1#form") == "http://x.ru/?a=1#form"


def test_links_keep_the_params_that_stay_as_written():
    # a query that is not UTF-8 (an old cp1251 site) is not decoded and written again
    assert parsers._without_campaign_tags("http://x.ru/?utm_source=2gis&city=%CC%EE%F1%EA%E2%E0") == "http://x.ru/?city=%CC%EE%F1%EA%E2%E0"
    assert parsers._without_campaign_tags("http://x.ru/index.php?name=%EA%E0%F4%E5") == "http://x.ru/index.php?name=%EA%E0%F4%E5"
    assert parsers._without_campaign_tags("https://x.ru/menu?pdf&UTM_Medium=a") == "https://x.ru/menu?pdf"
    assert parsers._without_prefilled_text("https://wa.me/7495?text=%D0%97%D0%B4%21&a=b%20c") == "https://wa.me/7495?a=b%20c"


def test_a_contact_that_is_no_link_is_not_made_into_one():
    raw = {"contact_groups": [{"contacts": [
        {"type": "fax", "text": "+7 (383) 363‒05‒56", "value": "+73833630556"},
        {"type": "pobox", "text": "а/я 45", "value": "а/я 45"},
        {"type": "skype", "text": "my.shop", "value": "my.shop"},
        {"type": "viber", "text": "viber://chat?number=%2B79991234567", "value": "viber://chat?number=%2B79991234567"},
        {"type": "vkontakte", "text": "https://vk.ru/shop", "value": "https://vk.ru/shop"},
        {"type": "telegram", "url": "https://t.me/shop", "text": "@shop", "value": "@shop"},
    ]}]}
    assert parsers.parse_contacts(raw)["social"] == [
        {"type": "viber", "link": "viber://chat?number=%2B79991234567"}, {"type": "vkontakte", "link": "https://vk.ru/shop"},
        {"type": "telegram", "link": "https://t.me/shop"}]


def test_place_emails_in_search_rows():
    rows = parsers.parse_search(load("search_dentist_moscow_contacts"), 1, 3)["places"]
    assert rows[0]["email"] == "clinic@k31.ru" and rows[0]["emails"] == ["clinic@k31.ru"]
    assert rows[0]["phones"][0]["comment"] == "единая справочная: круглосуточно"
    assert all(r["website"] and "utm_" not in r["website"] for r in rows)


def test_opening_hours():
    hours = place("place_chain_bar")["opening_hours"]
    assert [d["day"] for d in hours["days"]] == ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    # past midnight: the site writes the closing time of the next morning
    assert hours["days"][0]["hours"] == [{"from": "18:00", "to": "06:00"}]
    assert hours["is_open_24_7"] is False
    around_the_clock = parsers.parse_opening_hours({"Mon": {"working_hours": [{"from": "00:00", "to": "24:00"}]},
                                                    "is_24x7": True, "comment": "по записи"})
    assert around_the_clock["is_open_24_7"] is True and around_the_clock["note"] == "по записи"
    assert around_the_clock["days"][1] == {"day": "tuesday", "hours": []}
    assert parsers.parse_opening_hours(None) is None


def test_chain_and_brand():
    p = place("place_chain_bar", domain="ru")
    assert p["chain"] == {"id": "70000001034739764", "name": "Руки Вверх!", "full_name": "Руки Вверх!, бар",
                          "name_extension": "бар", "branch_count": 9,
                          "link": "https://2gis.ru/moscow/branches/70000001034739764"}
    assert p["brand"]["name"] == "Руки Вверх!" and p["brand"]["branch_count"] == 9
    # without the country site there is no branches page to link
    assert place("place_chain_bar")["chain"]["link"] is None
    assert place("place_chain_bar")["link"] == f"https://2gis.com/firm/{BAR}"


def test_values_the_page_does_not_print_are_off_by_default():
    p = place("place_chain_bar")
    assert "rating" not in p["chain"] and "review_count" not in p["chain"]
    assert "advertiser_legal_name" not in p["promotion"]
    assert not any(key == "advertiser_legal_name" for key, _ in walk(p))
    on = place("place_chain_bar", chain_rating=True, advertiser_legal_name=True)
    assert on["chain"]["rating"] == 4.7 and on["chain"]["review_count"] > 4000
    assert "Рв Москва" in on["promotion"]["advertiser_legal_name"]


def test_promotion_and_order_links():
    promo = place("place_chain_bar")["promotion"]
    assert promo["text"] == "Закати праздник по спец.цене!"
    assert promo["disclaimer"].startswith("*26.01.26")
    # one button per platform upstream: repeats are dropped, the click counter is unwrapped
    assert len(promo["actions"]) == len({json.dumps(a, sort_keys=True) for a in promo["actions"]})
    assert all("link.2gis" not in (a["link"] or "") for a in promo["actions"])
    assert promo["logo"].startswith("https://")
    orders = place("place_restaurant")["order_links"]
    assert orders and orders[0]["partner"].startswith("Яндекс.Еда") and orders[0]["link"].startswith("http")
    assert place("place_sparse")["order_links"] == []


def test_features_carry_the_filter_tag():
    p = place("place_chain_bar")
    tags = {f["tag"] for f in p["features"]}
    assert "food_service_avg_price" in tags
    assert {"group": "Ресторан / Кафе", "name": "Чек 1500 ₽", "tag": "food_service_avg_price", "is_award": False} in p["features"]
    assert parsers.feature_tags(load("place_chain_bar")["items"][0]) == tags
    assert "Живая музыка" in p["highlights"]


def test_stations_and_parking():
    p = place("place_chain_bar")
    station = p["nearest_stations"][0]
    assert station["name"] == "Пушкинская" and station["transport_types"] == ["metro"]
    assert isinstance(station["distance_meters"], int) and station["line"]
    assert p["parking"][0] == {"id": "4504656189325488", "name": "Парковка БЦ Summit", "capacity": 80, "is_paid": True,
                               "purpose": "car", "has_ev_charging": False}


def test_place_sparse():
    p = place("place_sparse", domain="ru")
    assert p["id"] == SPARSE and p["name"] == "ВкусВилл"
    assert p["rating"] is None and p["review_count"] is None and p["rating_count"] is None
    assert p["has_photos"] is False
    assert p["summary"] is None and p["description"] is None and p["promotion"] is None
    assert p["phones"] and p["website"] == "http://vkusvill.ru"
    assert p["chain"]["branch_count"] > 100
    # every key is there, whatever the record lacks
    assert set(p) == set(place("place_restaurant"))


def test_place_dubai_in_english():
    p = place("place_dubai_english", domain="ae")
    assert p["link"] == "https://2gis.ae/dubai/firm/70000001055853809"
    assert p["language"] == "en_AE"
    assert p["location"]["city"] == "Dubai" and p["location"]["makani"]
    assert p["location"]["building_name"] == "Mall of the Emirates"
    assert p["landmarks"] and isinstance(p["landmarks"][0]["distance_meters"], int)
    assert p["location"]["timezone_offset_minutes"] == 240


def test_place_building():
    p = place("place_building", domain="ru")
    assert p["type"] == "building" and p["name"] == "Summit, бизнес-центр"
    assert p["link"] == "https://2gis.ru/moscow/geo/4504235282991094"
    assert p["full_address"] == "Москва, Summit, бизнес-центр"
    b = p["building"]
    assert b["purpose"] == "Бизнес-центр" and b["floor_count"] == 10 and b["material"] == "Железобетон"
    assert b["business_count"] == 30 and b["internet_provider_count"] == 41
    assert b["services"][0]["name"] == "Почта России" and b["services"][0]["website"] == "https://pochta.ru/offices/125009"
    assert p["chain"] is None and p["phones"] == [] and p["website"] is None


def test_place_ids_and_junk():
    assert parsers.place_id("70000001057394703_wcj6cexc17d0dB9II5") == "70000001057394703"
    assert parsers.place_id("70000001057394703") == "70000001057394703"
    assert parsers.place_id(None) is None and parsers.place_id("") is None
    assert parsers.parse_place(None) is None
    assert parsers.parse_places([None, {}, {"id": "1_x", "region_id": 32}], {"32": "ru"})[0]["id"] == "1"
    assert parsers.parse_place({"id": "5"})["location"]["city"] is None


def test_batch_keeps_only_known_places():
    rows = parsers.parse_places(load("places_batch_three_one_unknown")["items"], {"32": "ru"})
    assert [r["id"] for r in rows] == [RESTAURANT, SPARSE]
    assert rows[0]["link"].startswith("https://2gis.ru/moscow/firm/")


def test_no_internal_noise_in_a_place():
    p = place("place_chain_bar", domain="ru")
    keys = {key for key, _ in walk(p)}
    assert not keys & {"url", "erid", "hash", "ads_id", "code", "rubric_ids", "segment_id", "stat"}
    assert not any(isinstance(value, str) and "link.2gis" in value for _, value in walk(p))
    assert not any(value == "" for _, value in walk(p))


# ---- a search page -------------------------------------------------------------------

def test_search_page():
    page = parsers.parse_search(load("search_cafe_moscow"), 1, 3, {"32": "ru"})
    assert page["pagination"] == {"page": 1, "items_per_page": 3, "total_pages": 2866, "total_count": 8597}
    assert len(page["places"]) == 3
    first = page["places"][0]
    assert "_" not in first["id"] and first["link"] == f"https://2gis.ru/moscow/firm/{first['id']}"
    filters = page["available_filters"]
    assert {"id": "161", "name": "Кафе", "count": 100} in filters["categories"]
    assert filters["cities"][0]["name"].startswith("Москва")
    assert all(d["id"] and d["name"] and d["city_id"] for d in filters["districts"])
    tags = {a["tag"]: a for a in filters["attributes"]}
    assert tags["food_service_avg_price"]["type"] == "range" and tags["food_service_avg_price"]["min"] > 0
    assert tags["covid_homedelivery"]["name"] == "Доставка"
    # the site's own controls (sort, district, worktime) are params, not attribute tags
    assert not {"rating", "district", "worktime", "bound"} & set(tags)


def test_search_in_another_country():
    page = parsers.parse_search(load("search_restaurant_dubai"), 1, 3, {"99": "ae"})
    assert page["pagination"]["total_count"] > 20000
    assert all(p["link"].startswith("https://2gis.ae/") for p in page["places"])
    assert all(p["language"] == "en_AE" for p in page["places"])


@pytest.mark.parametrize("limit, last_page", [(50, 200), (30, 334), (20, 500), (16, 625), (12, 834), (7, 1429), (3, 3334)])
def test_total_pages_stop_at_the_depth_the_site_serves(limit, last_page):
    # the last page that answered rows at each size, measured on 24,203 restaurants in the UAE (2026-11-23)
    page = parsers.parse_search({"total": 24203}, 1, limit)["pagination"]
    assert page["total_pages"] == last_page and page["total_count"] == 24203


def test_a_list_shorter_than_the_depth_keeps_its_pages():
    assert parsers.parse_search({"total": 8597}, 1, 20)["pagination"]["total_pages"] == 430
    assert parsers.parse_search({"total": 10000}, 1, 50)["pagination"]["total_pages"] == 200
    assert parsers.parse_search({"total": 10001}, 1, 50)["pagination"]["total_pages"] == 200


def test_has_prices_counts_a_price_list_shown_as_photos():
    # the site's "with prices" filter lists a place with goods and a place whose price list is a photo; the flag
    # says so for both (2026-11-24: 8 of 50 Moscow cafés under has_prices=true had only the photos)
    assert load("place_restaurant")["items"][0]["has_goods"] is False
    assert place("place_restaurant")["has_prices"] is True                  # a price list among its photos
    assert place("place_chain_bar")["has_prices"] is True                   # goods and a price list
    assert place("place_dubai_english")["has_prices"] is False              # neither
    rows = parsers.parse_search(load("search_restaurant_dubai"), 1, 3)["places"]
    assert [r["has_prices"] for r in rows] == [False, True, True]          # neither, goods, a price list
    assert parsers.lists_prices({}) is False
    assert parsers.lists_prices({"has_goods": True}) is True
    assert parsers.lists_prices({"object_flags": {"has_products": {}}}) is True
    assert parsers.lists_prices({"object_flags": {"has_price_list_images": {}}}) is True
    assert parsers.lists_prices({"has_goods": False, "object_flags": {"vacancies": {}}}) is False


def test_reviews_pages_are_not_capped():
    assert parsers.pagination(1, 50, 24203) == {"page": 1, "items_per_page": 50, "total_pages": 485, "total_count": 24203}


def test_review_sources_and_a_page_without_reviews():
    payload = load("reviews_page_1")
    assert {"2gis", "flamp", "tbank"} <= set(parsers.review_sources(payload)) and "4sq" not in parsers.review_sources(payload)
    assert parsers.review_sources({}) == []
    empty = parsers.parse_reviews(parsers.without_reviews(payload), 1, 10)
    assert empty["reviews"] == [] and empty["pagination"]["total_count"] == 0 and empty["pagination"]["total_pages"] == 0
    assert empty["rating"] == 4.8 and empty["review_count"] == 1253 and empty["sources"] == parsers.review_sources(payload)
    assert len(payload["reviews"]) == 10        # the raw answer is left as it was


def test_search_with_nothing_on_the_page():
    empty = parsers.parse_search(None, 1, 20)
    assert empty["places"] == [] and empty["pagination"]["total_count"] == 0 and empty["pagination"]["total_pages"] == 0
    past_end = parsers.parse_search({"total": 8597}, 5000, 20)
    assert past_end["places"] == [] and past_end["pagination"] == {"page": 5000, "items_per_page": 20,
                                                                  "total_pages": 430, "total_count": 8597}
    assert empty["available_filters"] == {"categories": [], "cities": [], "districts": [], "attributes": []}


@pytest.mark.parametrize("name, code", [("search_past_end", 404), ("search_no_result", 404), ("place_not_found", 404),
                                        ("search_bad_param", 400)])
def test_catalog_error_bodies(name, code):
    body = load(name)
    assert body["meta"]["code"] == code and "result" not in body


def test_chain_branches():
    page = parsers.parse_search(load("chain_branches"), 1, 50, {"32": "ru"})
    assert page["pagination"]["total_count"] == 9 and len(page["places"]) == 9
    assert {p["chain"]["id"] for p in page["places"]} == {"70000001034739764"}
    assert BAR in {p["id"] for p in page["places"]}


def test_city_match():
    assert parsers.parse_city_match(load("city_krasnogorsk")["items"]) == {
        "city_id": "4504222397629287", "name": "Krasnogorsk", "slug": "krasnogorsk", "region_id": "32"}
    # a name the site does not have is answered with a village in region 0: not a match
    assert parsers.parse_city_match(load("city_new_york_junk")["items"]) is None
    assert parsers.parse_city_match([]) is None and parsers.parse_city_match(None) is None


# ---- reviews -------------------------------------------------------------------------

def test_reviews_page():
    page = parsers.parse_reviews(load("reviews_page_1"), 1, 10)
    assert page["rating"] == 4.8 and page["is_rating_confirmed"] is True and page["review_count"] == 1253
    assert "2gis" in page["sources"] and "flamp" in page["sources"]
    assert page["pagination"] == {"page": 1, "items_per_page": 10, "total_pages": 120, "total_count": 1199}
    review = page["reviews"][0]
    assert review["id"] == "318945544" and review["rating"] == 5 and review["text"] == "Супер !"
    assert review["source"] == "2gis" and review["place_id"] == BAR
    # +07:00 upstream -> UTC
    assert review["created_at"] == "2026-11-19T13:06:21Z" and review["edited_at"] is None
    assert review["is_confirmed"] is True and review["is_hidden"] is False
    assert review["author"]["name"] == "Елена Кучеряева"
    assert review["author"]["link"] == "https://2gis.ru/user/fc909b86d5e34d39b7283c7cdfc68a70"
    assert review["author"]["review_count"] == 16
    assert len(review["photos"]) == 4 and all(p.startswith("https://") for p in review["photos"])
    assert review["owner_reply"]["organization"] == "Руки Вверх!, бар" and review["owner_reply"]["created_at"].endswith("Z")
    visited = next(r for r in page["reviews"] if r["visit_count"])
    assert visited["visit_count"] == 1 and visited["trust_note"]


def test_reviews_leave_out_what_the_page_does_not_show():
    for name in ("reviews_page_1", "reviews_flamp", "reviews_without_text"):
        page = parsers.parse_reviews(load(name), 1, 10)
        keys = {key for key, _ in walk(page)}
        assert not keys & {"ip", "user_agent", "email", "last_visit", "additional_data", "items"}
        # the reviewer's internal account number stays out; the public profile id is the handle
        assert all(set(r["author"]) == {"name", "first_name", "last_name", "public_id", "link", "photo", "review_count", "source"}
                   for r in page["reviews"])


def test_a_review_without_stars_has_no_rating():
    raw = load("reviews_page_1")["reviews"][0]
    assert parsers.parse_review(raw)["rating"] == 5
    assert parsers.parse_review({**raw, "rating": 0})["rating"] is None      # how a Foursquare tip comes
    assert parsers.parse_review({**raw, "rating": None})["rating"] is None
    assert parsers.parse_review({k: v for k, v in raw.items() if k != "rating"})["rating"] is None


def test_reviews_from_another_source():
    page = parsers.parse_reviews(load("reviews_flamp"), 1, 5)
    review = page["reviews"][0]
    assert review["source"] == "flamp" and review["source_link"].startswith("https://flamp.ru/r/")
    assert review["author"]["public_id"] is None and review["author"]["link"] == "http://flamp.ru/user2592798"
    assert review["reactions"] == [] or all(set(r) == {"type", "count"} for r in review["reactions"])


def test_reviews_without_text_and_last_page():
    silent = parsers.parse_reviews(load("reviews_without_text"), 1, 6)["reviews"]
    assert any(r["text"] is None and r["rating"] for r in silent)
    last = parsers.parse_reviews(load("reviews_last_page"), 120, 10)
    assert len(last["reviews"]) == 9 and last["pagination"]["total_pages"] == 120


def test_reviews_unknown_place_and_place_without_reviews():
    unknown, quiet = load("reviews_unknown_place"), load("reviews_place_without_reviews")
    assert parsers.is_known_place(unknown) is False
    assert parsers.is_known_place(quiet) is True
    page = parsers.parse_reviews(quiet, 1, 20)
    assert page["reviews"] == [] and page["rating"] is None and page["review_count"] == 0
    assert page["pagination"]["total_count"] == 0


def test_review_extras():
    topics = parsers.parse_review_topics(load("review_topics"))
    assert topics[0] == {"name": "музыка", "is_pinned": False} and len(topics) > 3
    counts = parsers.parse_rating_counts(load("review_ratings"))
    assert counts == [{"rating": 5, "count": 1274}, {"rating": 4, "count": 97}, {"rating": 3, "count": 67},
                      {"rating": 2, "count": 41}, {"rating": 1, "count": 130}]
    assert parsers.parse_review_summary(load("review_summary")).startswith("Этот ресторан")
    assert parsers.parse_review_summary({"text": None}) is None
    assert parsers.parse_review_topics({"facts": []}) == [] and parsers.parse_rating_counts({}) == []


# ---- photos --------------------------------------------------------------------------

def test_photos_page():
    page = parsers.parse_photos(load("photos_page_1"))
    assert page["albums"][0] == {"id": "all", "name": "Все", "count": 492}
    assert len(page["photos"]) == 8 and page["next_cursor"].count(".") == 2
    photo = next(p for p in page["photos"] if p["type"] == "photo")
    assert photo["link"].startswith("https://") and photo["width"] > 0 and photo["height"] > 0
    assert photo["thumbnail"].startswith("https://") and photo["created_at"].endswith("Z")
    assert photo["duration_seconds"] is None and photo["video_streams"] == []
    by_a_guest = next(p for p in page["photos"] if p["author"])
    assert by_a_guest["author"] == {"name": "Артем Волченко", "public_id": "0329c8616788461e830b5d7d7886d672",
                                    "link": "https://2gis.ru/user/0329c8616788461e830b5d7d7886d672", "photo": None}
    assert by_a_guest["source"] == "user"


def test_videos():
    page = parsers.parse_photos(load("photos_videos"))
    video = page["photos"][0]
    assert video["type"] == "video" and video["duration_seconds"] == 5
    assert {s["type"] for s in video["video_streams"]} == {"hls", "dash"}
    assert video["link"] == video["video_streams"][0]["link"]
    assert video["width"] == 720 and video["height"] == 960 and video["thumbnail"].startswith("https://")
    assert parsers.parse_photos({"items": [], "albums": []}) == {"albums": [], "photos": [], "next_cursor": None}


# ---- suggestions, cities, categories -------------------------------------------------

def test_suggestions():
    rows = parsers.parse_suggestions(load("suggestions_coffee")["items"], "ru")
    assert rows[0] == {**rows[0], "type": "category", "text": "кофейни", "id": "162", "name": "Кофейни", "place_count": 3690}
    kinds = {r["type"] for r in rows}
    assert kinds == {"category", "category_group", "chain", "place", "query", "filter"}
    chain = next(r for r in rows if r["type"] == "chain")
    assert chain["link"].startswith("https://2gis.ru/moscow/branches/") and chain["place_count"] == 43
    spot = next(r for r in rows if r["type"] == "place")
    assert spot["link"] == f"https://2gis.ru/moscow/firm/{spot['id']}" and "_" not in spot["id"] and spot["address"]
    phrase = next(r for r in rows if r["type"] == "query")
    assert phrase["text"] == "кофейня рядом" and phrase["id"] is None
    assert len({frozenset(r) for r in rows}) == 1        # one shape for every kind
    assert parsers.parse_suggestions(None) == []


def test_cities():
    cities = parsers.parse_cities(load("regions_english")["items"])
    assert len(cities) == 208
    by_slug = {c["slug"]: c for c in cities}
    uae = by_slug["dubai"]
    assert uae["id"] == "99" and uae["name"] == "UAE" and uae["country_code"] == "AE" and uae["site"] == "2gis.ae"
    assert uae["link"] == "https://2gis.ae/dubai" and uae["languages"] == ["en_AE", "ar_AE"]
    assert uae["timezone"] == "Asia/Dubai" and uae["timezone_offset_minutes"] == 240
    assert uae["place_count"] > 400000 and "metro" in uae["features"]
    assert {"name": "Sharjah", "latitude": 25.349027, "longitude": 55.388262} in uae["towns"]
    moscow = by_slug["moscow"]
    assert moscow["id"] == "32" and moscow["site"] == "2gis.ru" and "en_RU" in moscow["languages"]
    assert by_slug["cyprus"]["site"] == "2gis.com.cy"
    assert not any(key in ("bounds", "geometry") for key, _ in walk(cities))


def test_categories_tree():
    tree = parsers.parse_categories_tree(load("categories_moscow")["items"], "ru", "moscow")
    assert len(tree) == 28 and sum(len(g["subcategories"]) for g in tree) == 1669
    food = next(g for g in tree if g["id"] == "2")
    assert food["name"].startswith("Досуг") and food["place_count"] > 70000 and "link" not in food
    coffee = next(c for c in food["subcategories"] if c["id"] == "162")
    assert coffee == {"id": "162", "name": "Кофейни", "slug": "kofejjni",
                      "link": "https://2gis.ru/moscow/search/kofejjni/rubricId/162",
                      "place_count": coffee["place_count"], "company_count": coffee["company_count"]}
    assert coffee["place_count"] > 3000
    assert parsers.parse_categories_tree([]) == []


# ---- refs ----------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    RESTAURANT,
    f"{RESTAURANT}_wcj6cexc17d0dB9II5G4H3JJI1IH",
    f"https://2gis.ru/moscow/firm/{RESTAURANT}",
    f"https://2gis.ru/moscow/firm/{RESTAURANT}/tab/reviews",
    f"https://2gis.ru/moscow/firm/{RESTAURANT}/37.646903%2C55.746418?m=37.6,55.7%2F16",
    f"2gis.ae/dubai/firm/{RESTAURANT}",
    f"https://2gis.com/firm/{RESTAURANT}",
    f"https://2gis.com.cy/cyprus/firm/{RESTAURANT}",
    f"https://2gis.kz/almaty/geo/{RESTAURANT}",
])
def test_resolve_place(value):
    assert refs.resolve_place(value) == RESTAURANT


@pytest.mark.parametrize("value", ["abc", "", "12", "https://example.com/firm/70000001057394703",
                                   "https://2gis.ru/moscow/search/кафе", "https://go.2gis.com/abcd",
                                   "https://2gis.ru.evil.com/firm/70000001057394703"])
def test_resolve_place_rejects(value):
    with pytest.raises(ValueError):
        refs.resolve_place(value)


def test_resolve_places():
    assert refs.resolve_places(f"{RESTAURANT}, https://2gis.ru/moscow/firm/{BAR},{RESTAURANT}") == [RESTAURANT, BAR]
    with pytest.raises(ValueError, match="At most 100"):
        refs.resolve_places(",".join(str(70000001000000000 + i) for i in range(101)))
    with pytest.raises(ValueError):
        refs.resolve_places(" , ")


def test_resolve_chain():
    assert refs.resolve_chain(BAR) == {"place": BAR}
    assert refs.resolve_chain(f"https://2gis.ru/moscow/firm/{BAR}") == {"place": BAR}
    assert refs.resolve_chain("https://2gis.ru/moscow/branches/70000001034739764") == {"chain": "70000001034739764", "city": "moscow"}
    with pytest.raises(ValueError):
        refs.resolve_chain("https://2gis.ru/moscow/search/кафе")


def test_resolve_city():
    assert refs.resolve_city("  Moscow ") == "Moscow"
    assert refs.resolve_city("https://2gis.ae/dubai/search/restaurant") == "dubai"
    assert refs.resolve_city("https://2gis.ru/moscow") == "moscow"
    with pytest.raises(ValueError):
        refs.resolve_city("https://2gis.ru/firm/70000001057394703")
    with pytest.raises(ValueError):
        refs.resolve_city("x" * 81)
    assert refs.city_key("Санкт-Петербург") == refs.city_key("санкт петербург")
    assert refs.city_key("Орёл") == refs.city_key("орел")


def test_links():
    assert refs.place_link("5", "ae", "dubai") == "https://2gis.ae/dubai/firm/5"
    assert refs.place_link("5") == "https://2gis.com/firm/5"
    assert refs.place_link("5", "ru", "moscow", "building") == "https://2gis.ru/moscow/geo/5"
    assert refs.place_link(None) is None
    assert refs.branches_link("7", "ru", "moscow") == "https://2gis.ru/moscow/branches/7"
    assert refs.branches_link("7") is None
    assert refs.user_link(None) is None
    wrapped = "http://link.2gis.ru/1.2/E796C9F2/online/20261101/project32/7/null/Ao5d?http://www.deepfriedfriends.ru/"
    assert refs.target_of_redirect(wrapped) == "http://www.deepfriedfriends.ru/"
    assert refs.target_of_redirect("https://t.me/dffradio") == "https://t.me/dffradio"
    assert refs.target_of_redirect(None) is None


# ---- the request signature -----------------------------------------------------------

SALT = "baf4c54e9dae"       # the public salt of the site's app script the checksums below were made with


def test_signature_matches_the_site():
    # three calls the site's own pages sent on 2026-11-21, with the `r` each carried
    params = {"key": "c7f1a769-c8a5-4636-b14d-d8c987808a12", "user_id": "918d3568-b7d3-4bc4-bf98-0d551e94296b",
              "stat[sid]": "6733ec51-d74f-40ac-bf11-07974dc16a8e", "stat[user]": "918d3568-b7d3-4bc4-bf98-0d551e94296b",
              "shv": "2026-11-20-16"}
    assert fetch.signature("/3.0/profile/data", params, SALT) == 563519944
    # non-ASCII values are hashed as UTF-16 code units, the way the site's script reads them
    assert fetch.signature("/3.0/items", {"q": "кафе", "key": "k"}, SALT) == fetch.signature("/3.0/items", {"key": "k", "q": "кафе"}, SALT)
    assert fetch.signature("/3.0/items", {"q": "кафе", "key": "k"}, SALT) != fetch.signature("/3.0/items", {"q": "кафе", "key": "j"}, SALT)


def test_signed_link():
    link = fetch.signed_link("/3.0/items/byid", {"id": "1,2", "locale": None, "fields": "items.point"}, "KEY", SALT)
    assert link.startswith("https://catalog.api.2gis.ru/3.0/items/byid?id=1,2&fields=items.point&key=KEY&r=")
    assert "locale" not in link
    r = int(link.rsplit("&r=", 1)[1])
    assert r == fetch.signature("/3.0/items/byid", {"id": "1,2", "fields": "items.point", "key": "KEY"}, SALT)
    spaced = fetch.signed_link("/3.0/items", {"q": "кафе рядом"}, "KEY", SALT)
    assert "q=%D0%BA%D0%B0%D1%84%D0%B5%20%D1%80%D1%8F%D0%B4%D0%BE%D0%BC" in spaced


def test_search_params():
    params = fetch.search_params("кафе", region_id="32", page=2, page_size=80, sort="relevance",
                                 category_ids=["161", "164"], filters={"attr[wifi]": "true"})
    assert params["page_size"] == 50 and params["page"] == 2 and params["type"] == "branch"
    assert params["sort"] is None and params["rubric_id"] == "161,164" and params["attr[wifi]"] == "true"
    near = fetch.search_params("кафе", point=(37.6, 55.7), radius=500, sort="distance")
    assert near["point"] == "37.6,55.7" and near["radius"] == 500 and near["sort_point"] == "37.6,55.7"
    chain = fetch.search_params(region_id="32", chain_id="7")
    assert chain["org_id"] == "7" and chain["search_disable_clipping_by_relevance"] == "true" and chain["q"] is None


def test_parse_keys():
    page = '<script>var x={"webApiKey":"aaaaaaaa-1111","reviewApiKey":"bbbbbbbb-2222","photoApiKey":"cccc33"}</script>' \
           '<script src="https://d-assets.2gis.ru/app.0e61cf77090817b6d576.js"></script>'
    script = 'class nM{constructor(e,t){this.request=e,this.KEY=t.webApiKey,this.a="0123abcd4567";const r=1}}'
    assert fetch.app_script_link(page) == "https://d-assets.2gis.ru/app.0e61cf77090817b6d576.js"
    assert fetch.parse_keys(page, script) == {"catalog": "aaaaaaaa-1111", "reviews": "bbbbbbbb-2222",
                                              "photos": "cccc33", "salt": "0123abcd4567"}
    with pytest.raises(fetch.TwoGisUpstreamError):
        fetch.parse_keys(page, "no salt here")
    with pytest.raises(fetch.TwoGisUpstreamError):
        fetch.parse_keys("<html></html>", script)
