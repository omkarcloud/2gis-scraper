"""Marshmallow request schemas for every /2gis/* route.

ONE param per input (never a sibling `url` / `id` pair):

    id      a place id (70000001057394703) OR a 2GIS place link; on
            places/branches also a chain's "all branches" link
    ids     up to 100 of those, comma-separated
    city    a city name in any language the site knows (Moscow, Москва,
            Sharjah), a 2GIS city slug (moscow) OR a 2GIS link with a city

A search needs `query` or `category_id`. It is placed by `city`, by
`latitude` + `longitude` (+ `radius`), by `polygon`, or by a place named in
the query itself ("кафе Новосибирск"). The site serves at most 50 places
and 50 reviews per page and 100 photos per call.
"""
import re

from marshmallow import ValidationError, fields, post_load, pre_load, validate, validates_schema

from schema_fields import (BaseSchema, ChoiceField, CountryCodeField, DateField, Flag, LimitField, PageField,
                           PositiveInt, QueryField, RefField, StrippedString)
from scraper_utils.schema import max_length as _max_length
from two_gis import refs

MAX_LIMIT = 50              # the catalog and the reviews API refuse more than 50 a page
MAX_PHOTOS_LIMIT = 100      # the photo API refuses more than 100 a call
MAX_PAGE = 10000            # a search is served to its 10,000th row (refs.MAX_SEARCH_ROWS), page 10,000 at limit 1; past the end is an empty page
MAX_IDS_PER_FILTER = 20
MAX_TOPIC_LENGTH = 80
MAX_CURSOR_LENGTH = 2000

_ID_LIST_RE = re.compile(r"^[0-9]{1,20}$")
_POLYGON_RE = re.compile(r"^POLYGON\s*\(\(\s*-?[0-9.]+ -?[0-9.]+(\s*,\s*-?[0-9.]+ -?[0-9.]+){3,200}\s*\)\)$", re.I)
_CURSOR_RE = re.compile(r"^[A-Za-z0-9_.=-]+$")
_RANGE_RE = re.compile(r"^(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)$")


class PlaceRefField(RefField):
    resolver = staticmethod(refs.resolve_place)


class PlacesRefField(RefField):
    """'<id>, <link>, <id>' -> the place ids, in order, repeats dropped."""
    resolver = staticmethod(refs.resolve_places)


class ChainRefField(RefField):
    resolver = staticmethod(refs.resolve_chain)


class CityField(RefField):
    """Optional unless required=True is passed."""
    resolver = staticmethod(refs.resolve_city)

    def __init__(self, **kwargs):
        if not kwargs.setdefault("required", False):
            kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)


class Schema(BaseSchema):
    """A param sent empty (`?city=&page=`) is a param not sent."""

    @pre_load
    def _drop_empty(self, data, **kwargs):
        return {key: value for key, value in dict(data).items() if not (isinstance(value, str) and not value.strip())}


class DefaultedChoice(ChoiceField):
    """A choice whose empty value means its default, as a missing one does
    (StrippedString hands back None for `?sort=`)."""

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None and isinstance(self.load_default, str):
            return self.load_default
        return value


class IdListField(fields.Field):
    """'161, 164' -> ['161', '164']: the site's numeric ids, repeats dropped."""

    def __init__(self, max_items=MAX_IDS_PER_FILTER, **kwargs):
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)
        self.max_items = max_items

    def _deserialize(self, value, attr, data, **kwargs):
        ids = []
        for raw in str(value).split(","):
            item = raw.strip()
            if not item:
                continue
            if not _ID_LIST_RE.match(item):
                raise ValidationError(f"'{item}' is not an id (digits only).")
            if item not in ids:
                ids.append(item)
        if not ids:
            return None
        if len(ids) > self.max_items:
            raise ValidationError(f"At most {self.max_items} ids.")
        return ids


class WordListField(fields.Field):
    """'2gis, Flamp' -> ['2gis', 'flamp'], each one of `allowed`; a word in `aliases` stands for its value."""

    def __init__(self, allowed, aliases=None, **kwargs):
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)
        self.allowed = tuple(allowed)
        self.aliases = dict(aliases or {})

    def _deserialize(self, value, attr, data, **kwargs):
        words = []
        for raw in str(value).split(","):
            item = raw.strip().lower()
            if not item:
                continue
            item = self.aliases.get(item, item)
            if item not in self.allowed:
                raise ValidationError(f"'{item}' must be one of: {', '.join((*self.allowed, *self.aliases))}.")
            if item not in words:
                words.append(item)
        return words or None


class ReviewRatingField(fields.Field):
    """'5', '1,2' -> ['1', '2']; or one of positive / neutral / negative -> [that word]."""

    def __init__(self, **kwargs):
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        items = [raw.strip().lower() for raw in str(value).split(",") if raw.strip()]
        if not items:
            return None
        if len(items) == 1 and items[0] in refs.REVIEW_RATING_GROUPS:
            return items
        if all(item in ("1", "2", "3", "4", "5") for item in items):
            return sorted(set(items))
        raise ValidationError("Must be star ratings from 1 to 5, comma-separated (1,2), "
                              f"or one of: {', '.join(refs.REVIEW_RATING_GROUPS)}.")


class MinRatingField(fields.Field):
    """'4', '4.0', '4.5' -> the rating floor as the site has it (3, 3.5, 4, 4.5, 4.9)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        text = str(value).strip().replace(",", ".")
        if not text:
            return None
        try:
            key = f"{float(text):g}"
        except ValueError:
            key = None
        if key not in refs.MIN_RATINGS:
            raise ValidationError(f"Must be one of: {', '.join(refs.MIN_RATINGS)}.")
        return key


class AttributesField(fields.Field):
    """'food_service_food_italian, food_service_avg_price:300-600' ->
    [[tag, None], [tag, [300, 600]]]: the attribute tags a search answer
    lists under available_filters.attributes; a range attribute takes
    min-max after a colon."""

    def __init__(self, **kwargs):
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        parsed = []
        for raw in str(value).split(","):
            item = raw.strip()
            if not item:
                continue
            tag, sep, span = item.partition(":")
            tag = tag.strip()       # as the answer writes it: a few tags carry a capital letter
            if not refs.ATTRIBUTE_RE.match(tag):
                raise ValidationError(f"'{item}': a tag is letters, digits, underscores and hyphens, written as "
                                      "available_filters.attributes lists it (food_service_food_italian).")
            bounds = None
            if sep:
                match = _RANGE_RE.match(span.strip())
                if not match or float(match.group(1)) > float(match.group(2)):
                    raise ValidationError(f"'{item}': a range is written tag:min-max (food_service_avg_price:300-600).")
                bounds = [match.group(1), match.group(2)]
            if all(tag != seen for seen, _ in parsed):
                parsed.append([tag, bounds])
        if not parsed:
            return None
        if len(parsed) > refs.MAX_ATTRIBUTES:
            raise ValidationError(f"At most {refs.MAX_ATTRIBUTES} attributes.")
        return parsed


class PolygonField(StrippedString):
    """A WKT polygon, longitude first: POLYGON((37.60 55.76,37.63 55.76,37.63 55.75,37.60 55.76))."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None:
            return None
        if not _POLYGON_RE.match(value):
            raise ValidationError("Must be a WKT polygon, longitude first, closed on its first point: "
                                  "POLYGON((37.60 55.76,37.63 55.76,37.63 55.75,37.60 55.76)).")
        return value


class CursorField(StrippedString):
    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None:
            return None
        if len(value) > MAX_CURSOR_LENGTH or not _CURSOR_RE.match(value):
            raise ValidationError("Must be the `next_cursor` of the previous answer.")
        return value


def _language(what):
    return ChoiceField(refs.LANGUAGES, metadata={"description": what})


def _coordinate(limit, what):
    return fields.Float(load_default=None, metadata={"description": what},
                        validate=validate.Range(min=-limit, max=limit, error=f"Must be between -{limit} and {limit}."))


def _flag(what):
    return Flag(metadata={"description": what})


# ---- places --------------------------------------------------------------------------

class PlacesSearchSchema(Schema):
    query = QueryField(required=False, load_default=None, validate=_max_length(200),
                       metadata={"description": "what to look for (кафе, dentist, a company name); needed unless "
                                                "category_id is given"})
    city = CityField(metadata={"description": "city name in any language (Moscow, Москва, Sharjah), a 2GIS city slug "
                                              "(moscow) or a 2GIS link; cities/list names every one"})
    latitude = _coordinate(90, "search around this point (with longitude)")
    longitude = _coordinate(180, "search around this point (with latitude)")
    radius = PositiveInt(max_value=refs.MAX_RADIUS,
                         metadata={"description": "metres around the point, up to 50000 (default 1000)"})
    polygon = PolygonField(metadata={"description": "search inside a WKT polygon, longitude first: "
                                                    "POLYGON((37.60 55.76,37.63 55.76,37.63 55.75,37.60 55.76))"})
    category_id = IdListField(metadata={"description": "category ids, comma-separated (162 = coffee shops in "
                                                       "Moscow); categories/list and a search's available_filters "
                                                       "list them"})
    district_id = IdListField(metadata={"description": "district ids, comma-separated, from available_filters.districts"})
    metro_id = IdListField(metadata={"description": "metro station ids, comma-separated, from a place's nearest_stations"})
    sort = DefaultedChoice(dict(refs.SORTS), load_default=refs.SORTS["relevance"],
                           metadata={"description": "relevance (default), rating, newest, name or distance "
                                                    "(distance needs latitude and longitude)"})
    min_rating = MinRatingField(metadata={"description": "only places rated at least 3, 3.5, 4, 4.5 or 4.9"})
    has_website = _flag("only places with a website")
    has_photos = _flag("only places with photos")
    open_now = _flag("only places open right now")
    open_24h = _flag("only places open around the clock")
    delivery = _flag("only places that deliver")
    takeaway = _flag("only places with takeaway")
    card_payment = _flag("only places that take cards")
    wifi = _flag("only places with Wi-Fi")
    has_prices = _flag("only places that list prices: goods with prices, or a price list shown as photos")
    new_places = _flag("only places the site marks as new in the city")
    opened_after = DateField(bound="past", metadata={"description": "only places opened on or after this date (YYYY-MM-DD)"})
    attributes = AttributesField(metadata={"description": "attribute tags from available_filters.attributes, "
                                                          "comma-separated; a range as tag:min-max "
                                                          "(food_service_food_italian,food_service_avg_price:300-600)"})
    language = _language("answer language (ru, en, ar, kk, uz...); needs city, and must be a language of the city's "
                         "country. Default: the city's own")
    page = PageField(max_page=MAX_PAGE, metadata={"description": "page number, from 1; a search serves its first "
                                                                 "10,000 places"})
    limit = LimitField(default=20, max_size=MAX_LIMIT, metadata={"description": "places per page, up to 50"})

    @validates_schema
    def _check(self, data, **kwargs):
        if not data.get("query") and not data.get("category_id"):
            raise ValidationError("Give a query or a category_id.", "query")
        has_lat, has_lon = data.get("latitude") is not None, data.get("longitude") is not None
        if has_lat != has_lon:
            raise ValidationError("latitude and longitude go together.", "latitude" if has_lon else "longitude")
        if data.get("radius") and not has_lat:
            raise ValidationError("radius needs latitude and longitude.", "radius")
        if data.get("polygon") and has_lat:
            raise ValidationError("Give a polygon or a point with a radius, not both.", "polygon")
        if data.get("sort") == refs.SORTS["distance"] and not has_lat:
            raise ValidationError("distance needs latitude and longitude.", "sort")

    @post_load
    def _default_radius(self, data, **kwargs):
        if data.get("latitude") is not None and not data.get("radius"):
            data["radius"] = refs.DEFAULT_RADIUS
        return data


class PlaceDetailsSchema(Schema):
    id = PlaceRefField(metadata={"description": "place id (70000001057394703) or a 2GIS place link"})
    language = _language("answer language (ru, en, ar, kk, uz...). Default: the place's own")


class PlacesBatchSchema(Schema):
    ids = PlacesRefField(metadata={"description": "up to 100 place ids or 2GIS place links, comma-separated"})
    language = _language("answer language (ru, en, ar, kk, uz...). Default: each place's own")


class PlacesBranchesSchema(Schema):
    id = ChainRefField(metadata={"description": "a place id or 2GIS place link (its chain is listed), or a chain's "
                                                "\"all branches\" link (https://2gis.ru/moscow/branches/<id>)"})
    city = CityField(metadata={"description": "list the chain's branches in this city; default: the city of the "
                                              "place or link given"})
    language = _language("answer language; must be a language of the city's country. Default: the city's own")
    page = PageField(max_page=MAX_PAGE, metadata={"description": "page number, from 1"})
    limit = LimitField(default=20, max_size=MAX_LIMIT, metadata={"description": "branches per page, up to 50"})


class PlacesReviewsSchema(Schema):
    id = PlaceRefField(metadata={"description": "place id (70000001057394703) or a 2GIS place link"})
    sort = DefaultedChoice(dict(refs.REVIEW_SORTS), load_default=refs.REVIEW_SORTS["newest"],
                           metadata={"description": "newest (default), edited (last edited first) or trusted (the "
                                                    "site's own order: reviews with a confirmed visit first)"})
    rating = ReviewRatingField(metadata={"description": "only these star ratings, comma-separated (1,2), or "
                                                        "positive, neutral or negative"})
    source = WordListField(refs.REVIEW_SOURCES, aliases=refs.REVIEW_SOURCE_ALIASES,
                           metadata={"description": "only reviews from these sources, comma-separated: 2gis, flamp, "
                                                    "booking, tbank, otello, netmonet, sbertips, avito, 4sq (or "
                                                    "foursquare, the name `review_sources` gives it); sources the "
                                                    "place's market does not have answer no reviews"})
    with_answer = _flag("only reviews the business replied to")
    without_text = _flag("also list ratings left without a text")
    topic = StrippedString(load_default=None, validate=_max_length(MAX_TOPIC_LENGTH),
                           metadata={"description": "only reviews that mention this topic, one of the `topics` the "
                                                    "first page returns"})
    unconfirmed = _flag("list the reviews the site has not confirmed (they do not count in the rating) instead")
    language = _language("language of the site's own labels (ru, en...); reviews stay as written")
    page = PageField(max_page=MAX_PAGE, metadata={"description": "page number, from 1"})
    limit = LimitField(default=20, max_size=MAX_LIMIT, metadata={"description": "reviews per page, up to 50"})


class PlacesPhotosSchema(Schema):
    id = PlaceRefField(metadata={"description": "place id (70000001057394703) or a 2GIS place link"})
    album = DefaultedChoice(list(refs.ALBUMS), load_default="all",
                            metadata={"description": "all (default), video, food_and_drinks, price_list_image (the "
                                                     "menu), interior, outside, owner, user...: an `id` from `albums`"})
    limit = LimitField(default=50, max_size=MAX_PHOTOS_LIMIT, metadata={"description": "photos per call, up to 100"})
    cursor = CursorField(metadata={"description": "`next_cursor` of the previous answer, for the next photos"})
    language = _language("language of the album names (ru, en...)")


class PlacesSuggestionsSchema(Schema):
    query = QueryField(max_length=100, metadata={"description": "the start of a search term"})
    city = CityField(required=True, metadata={"description": "city name, 2GIS city slug (moscow) or 2GIS link"})
    language = _language("answer language; must be a language of the city's country. Default: the city's own")


# ---- cities / categories -------------------------------------------------------------

class CitiesListSchema(Schema):
    language = DefaultedChoice(list(refs.LANGUAGES), load_default="en",
                               metadata={"description": "language of the names: en (default), ru, ar, kk, uz..."})
    country = CountryCodeField(metadata={"description": "only cities of this country, as a 2-letter code (RU, KZ, AE)"})


class CategoriesListSchema(Schema):
    city = CityField(required=True, metadata={"description": "city name, 2GIS city slug (moscow) or 2GIS link; "
                                                             "category ids and counts are per city"})
    language = _language("answer language; must be a language of the city's country. Default: the city's own")
