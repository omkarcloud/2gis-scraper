"""Offline tests for the /2gis/* request schemas."""
import pytest
from marshmallow import ValidationError

from two_gis import schemas as S

PLACE = "70000001057394703"


def load(schema, **query):
    return schema().load(query)


def errors(schema, **query):
    with pytest.raises(ValidationError) as caught:
        schema().load(query)
    return caught.value.normalized_messages()


# ---- places/search -------------------------------------------------------------------

def test_search_defaults():
    data = load(S.PlacesSearchSchema, query=" кафе ", city="Moscow")
    assert data["query"] == "кафе" and data["city"] == "Moscow"
    assert data["page"] == 1 and data["limit"] == 20 and data["sort"] == "relevance"
    assert data["latitude"] is None and data["radius"] is None and data["attributes"] is None
    assert data["has_website"] is None and data["language"] is None


def test_search_city_forms():
    assert load(S.PlacesSearchSchema, query="x", city="https://2gis.ae/dubai/search/restaurant")["city"] == "dubai"
    assert load(S.PlacesSearchSchema, query="x", city="Санкт-Петербург")["city"] == "Санкт-Петербург"
    assert "city" in errors(S.PlacesSearchSchema, query="x", city="https://example.com/moscow")


def test_search_sort_maps_to_the_site_token():
    assert load(S.PlacesSearchSchema, query="x", sort="newest")["sort"] == "opened_time"
    assert load(S.PlacesSearchSchema, query="x", sort="RATING")["sort"] == "rating"
    assert load(S.PlacesSearchSchema, query="x", sort="")["sort"] == "relevance"
    assert errors(S.PlacesSearchSchema, query="x", sort="bogus") == {
        "sort": ["Must be one of: relevance, rating, newest, name, distance."]}


def test_search_needs_a_query_or_a_category():
    assert errors(S.PlacesSearchSchema, city="moscow") == {"query": ["Give a query or a category_id."]}
    assert load(S.PlacesSearchSchema, category_id="162", city="moscow")["category_id"] == ["162"]


def test_search_point():
    data = load(S.PlacesSearchSchema, query="x", latitude="55.7558", longitude="37.6173")
    assert data["latitude"] == 55.7558 and data["longitude"] == 37.6173 and data["radius"] == 1000
    assert load(S.PlacesSearchSchema, query="x", latitude="55.7", longitude="37.6", radius="50000", sort="distance")["radius"] == 50000
    assert "longitude" in errors(S.PlacesSearchSchema, query="x", latitude="55.7")
    assert "latitude" in errors(S.PlacesSearchSchema, query="x", latitude="95", longitude="37.6")
    assert "radius" in errors(S.PlacesSearchSchema, query="x", latitude="55.7", longitude="37.6", radius="50001")
    assert "radius" in errors(S.PlacesSearchSchema, query="x", radius="500")
    assert errors(S.PlacesSearchSchema, query="x", sort="distance") == {"sort": ["distance needs latitude and longitude."]}


def test_search_polygon():
    wkt = "POLYGON((37.60 55.76,37.63 55.76,37.63 55.75,37.60 55.76))"
    assert load(S.PlacesSearchSchema, query="x", polygon=wkt)["polygon"] == wkt
    assert "polygon" in errors(S.PlacesSearchSchema, query="x", polygon="37.6,55.7;37.7,55.8")
    assert "polygon" in errors(S.PlacesSearchSchema, query="x", polygon=wkt, latitude="55.7", longitude="37.6")


def test_search_filters():
    data = load(S.PlacesSearchSchema, query="x", min_rating="4.0", has_website="true", wifi="0", open_now="yes",
                opened_after="2026-10-01", category_id="161, 164,161", district_id="4504209512726533",
                metro_id="4504385606391048")
    assert data["min_rating"] == "4" and data["has_website"] is True and data["wifi"] is False and data["open_now"] is True
    assert data["opened_after"] == "2026-10-01" and data["category_id"] == ["161", "164"]
    assert data["district_id"] == ["4504209512726533"] and data["metro_id"] == ["4504385606391048"]
    assert load(S.PlacesSearchSchema, query="x", min_rating="4,5")["min_rating"] == "4.5"
    assert errors(S.PlacesSearchSchema, query="x", min_rating="2") == {"min_rating": ["Must be one of: 3, 3.5, 4, 4.5, 4.9."]}
    assert "has_website" in errors(S.PlacesSearchSchema, query="x", has_website="maybe")
    assert "category_id" in errors(S.PlacesSearchSchema, query="x", category_id="coffee")
    assert "opened_after" in errors(S.PlacesSearchSchema, query="x", opened_after="01.10.2026")


def test_search_attributes():
    data = load(S.PlacesSearchSchema, query="x", attributes="food_service_food_italian, food_service_avg_price:300-600")
    assert data["attributes"] == [["food_service_food_italian", None], ["food_service_avg_price", ["300", "600"]]]
    # a tag is taken as the answer writes it: some carry a hyphen or a capital letter
    data = load(S.PlacesSearchSchema, query="x", attributes="dental_clinics_service_dentist-surgeon,"
                                                           "medical_test_med_analyzes_Children_tests")
    assert data["attributes"] == [["dental_clinics_service_dentist-surgeon", None],
                                  ["medical_test_med_analyzes_Children_tests", None]]
    assert "attributes" in errors(S.PlacesSearchSchema, query="x", attributes="italian food")
    assert "attributes" in errors(S.PlacesSearchSchema, query="x", attributes="food_service_avg_price:600-300")
    assert "attributes" in errors(S.PlacesSearchSchema, query="x", attributes="food_service_avg_price:cheap")
    assert "attributes" in errors(S.PlacesSearchSchema, query="x", attributes=",".join(f"tag_{i}" for i in range(11)))


def test_search_paging_limits():
    assert load(S.PlacesSearchSchema, query="x", page="173", limit="50")["limit"] == 50
    assert "limit" in errors(S.PlacesSearchSchema, query="x", limit="51")
    assert "page" in errors(S.PlacesSearchSchema, query="x", page="0")
    assert "language" in errors(S.PlacesSearchSchema, query="x", language="xx")
    assert load(S.PlacesSearchSchema, query="x", language="EN")["language"] == "en"


def test_empty_params_are_params_not_sent():
    # a caller that sends every param empty gets the defaults, not a 400
    data = load(S.PlacesSearchSchema, query="кафе", city="", latitude="", longitude="", radius="", sort="", min_rating="",
                has_website="", attributes="", language="", page="", limit="", category_id="", opened_after="", polygon="")
    assert data["city"] is None and data["page"] == 1 and data["limit"] == 20 and data["sort"] == "relevance"
    assert data["has_website"] is None and data["latitude"] is None
    assert errors(S.PlaceDetailsSchema, id="") == {"id": ["Missing data for required field."]}


def test_unknown_params_are_rejected():
    assert "citty" in errors(S.PlacesSearchSchema, query="x", citty="moscow")
    assert "url" in errors(S.PlaceDetailsSchema, id=PLACE, url="https://2gis.ru")


# ---- the place endpoints -------------------------------------------------------------

def test_details_id_or_link():
    assert load(S.PlaceDetailsSchema, id=PLACE) == {"id": PLACE, "language": None}
    assert load(S.PlaceDetailsSchema, id=f"https://2gis.ru/moscow/firm/{PLACE}/tab/reviews", language="ar") == {
        "id": PLACE, "language": "ar"}
    assert "2GIS place id" in errors(S.PlaceDetailsSchema, id="abc")["id"][0]
    assert errors(S.PlaceDetailsSchema, id="https://go.2gis.com/abcd")["id"][0].startswith("A go.2gis.com short link")
    assert errors(S.PlaceDetailsSchema) == {"id": ["Missing data for required field."]}


def test_batch():
    data = load(S.PlacesBatchSchema, ids=f"{PLACE}, https://2gis.ae/dubai/firm/70000001055853809, {PLACE}")
    assert data["ids"] == [PLACE, "70000001055853809"]
    many = ",".join(str(70000001000000000 + i) for i in range(101))
    assert errors(S.PlacesBatchSchema, ids=many) == {"ids": ["At most 100 places per call."]}
    assert len(load(S.PlacesBatchSchema, ids=many.rsplit(",", 1)[0])["ids"]) == 100


def test_branches():
    assert load(S.PlacesBranchesSchema, id=PLACE)["id"] == {"place": PLACE}
    data = load(S.PlacesBranchesSchema, id="https://2gis.ru/moscow/branches/70000001034739764", city="spb", page="2")
    assert data["id"] == {"chain": "70000001034739764", "city": "moscow"} and data["city"] == "spb" and data["page"] == 2
    assert "id" in errors(S.PlacesBranchesSchema, id="https://2gis.ru/moscow/search/кафе")
    assert "limit" in errors(S.PlacesBranchesSchema, id=PLACE, limit="51")


def test_reviews():
    data = load(S.PlacesReviewsSchema, id=PLACE)
    assert data["sort"] == "date_created" and data["page"] == 1 and data["limit"] == 20
    assert data["rating"] is None and data["source"] is None and data["unconfirmed"] is None
    assert load(S.PlacesReviewsSchema, id=PLACE, sort="trusted")["sort"] == "trust"
    assert load(S.PlacesReviewsSchema, id=PLACE, sort="edited")["sort"] == "date_edited"
    assert load(S.PlacesReviewsSchema, id=PLACE, rating="2, 1,2")["rating"] == ["1", "2"]
    assert load(S.PlacesReviewsSchema, id=PLACE, rating="Negative")["rating"] == ["negative"]
    assert load(S.PlacesReviewsSchema, id=PLACE, source="Flamp, 2gis")["source"] == ["flamp", "2gis"]
    assert load(S.PlacesReviewsSchema, id=PLACE, topic=" музыка ", with_answer="true")["topic"] == "музыка"
    assert "rating" in errors(S.PlacesReviewsSchema, id=PLACE, rating="9")
    assert "rating" in errors(S.PlacesReviewsSchema, id=PLACE, rating="positive,1")
    assert "source" in errors(S.PlacesReviewsSchema, id=PLACE, source="yelp")
    # a place's `review_sources` calls Foursquare `foursquare`; the reviews API calls it `4sq`: both are taken
    assert load(S.PlacesReviewsSchema, id=PLACE, source="foursquare")["source"] == ["4sq"]
    assert load(S.PlacesReviewsSchema, id=PLACE, source="Foursquare, 4sq")["source"] == ["4sq"]
    assert load(S.PlacesReviewsSchema, id=PLACE, source="2gis,foursquare")["source"] == ["2gis", "4sq"]
    assert "foursquare" in errors(S.PlacesReviewsSchema, id=PLACE, source="yelp")["source"][0]
    assert "sort" in errors(S.PlacesReviewsSchema, id=PLACE, sort="friends")
    assert "limit" in errors(S.PlacesReviewsSchema, id=PLACE, limit="51")
    assert "topic" in errors(S.PlacesReviewsSchema, id=PLACE, topic="x" * 81)


def test_photos():
    data = load(S.PlacesPhotosSchema, id=PLACE)
    assert data == {"id": PLACE, "album": "all", "limit": 50, "cursor": None, "language": None}
    assert load(S.PlacesPhotosSchema, id=PLACE, album="VIDEO", limit="100", cursor="aaa.bbb.c-c_c")["album"] == "video"
    assert "album" in errors(S.PlacesPhotosSchema, id=PLACE, album="bogus")
    assert "limit" in errors(S.PlacesPhotosSchema, id=PLACE, limit="101")
    assert "cursor" in errors(S.PlacesPhotosSchema, id=PLACE, cursor="not a cursor!")


def test_suggestions_cities_categories():
    assert load(S.PlacesSuggestionsSchema, query="кофе", city="moscow") == {"query": "кофе", "city": "moscow", "language": None}
    assert errors(S.PlacesSuggestionsSchema, query="кофе") == {"city": ["Missing data for required field."]}
    assert "query" in errors(S.PlacesSuggestionsSchema, city="moscow")
    assert load(S.CitiesListSchema) == {"language": "en", "country": None}
    assert load(S.CitiesListSchema, language="ru", country="kz") == {"language": "ru", "country": "KZ"}
    assert "country" in errors(S.CitiesListSchema, country="ZZ")
    assert load(S.CategoriesListSchema, city="Dubai", language="ar") == {"city": "Dubai", "language": "ar"}
    assert "city" in errors(S.CategoriesListSchema)


def test_every_field_is_described():
    for name in dir(S):
        schema = getattr(S, name)
        if isinstance(schema, type) and issubclass(schema, S.Schema) and schema is not S.Schema:
            for field_name, field in schema().fields.items():
                assert field.metadata.get("description"), f"{name}.{field_name} has no description"
