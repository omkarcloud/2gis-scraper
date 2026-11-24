"""Raw JSON -> clean dicts for every /2gis/* endpoint. Pure functions, no I/O.

The fixtures in two_gis/fixtures/ are the site's own answers, captured
through fetch.py on 2026-11-21 (the review summary on 2026-11-23):

  search_*.json(.gz)      the catalog's `result` of a search (3.0/items);
                          search_past_end / search_no_result /
                          search_bad_param / place_not_found are whole
                          bodies, with the `meta` that carries the outcome
  place_*.json            {"items": [...]} of 3.0/items/byid: a restaurant,
                          a chain's bar, a place with no rating and no
                          photos flag, an English-language place in Dubai,
                          a building
  chain_branches.json.gz  a chain's branches in one region
  city_*.json             the city lookup; regions_english, categories_moscow
  suggestions_coffee.json the suggestion box
  reviews_*.json          the reviews API: a first page, the last page, one
                          source (flamp), ratings without text, a place
                          without reviews, an unknown place
  review_topics / review_ratings / review_summary
  photos_page_1 / photos_videos   the photo API

What a place's page shows a signed-out visitor is returned. Two values the
catalog carries and the page does not print are left out unless the caller
of parse_place asks for them (api.py reads the switches from config): a
chain's own rating and review count (on since 2026-11-23, the owner's
decision), and the legal name of the advertiser behind a promoted card (off).
"""
import re
from urllib.parse import unquote_plus, urlsplit, urlunsplit

from scraper_utils.dates import iso_utc
from scraper_utils.text import clean, multiline
from scraper_utils.values import as_dict, as_list, g
from scraper_utils.values import number, to_int
from two_gis import refs


# ---- small pieces --------------------------------------------------------------------

def place_id(value):
    """'70000001057394703_<hash>' (a search row) or the bare id -> the id."""
    if not isinstance(value, str) or not value:
        return None
    return value.split("_", 1)[0] or None


def _dicts(value):
    return [item for item in as_list(value) if isinstance(item, dict)]


def _count(value):
    value = to_int(value)
    return value if value is not None and value >= 0 else None


def _rating(value):
    """A rating as the site prints it (4.8); 0 and missing -> None."""
    value = number(value)
    return value if value else None


def lists_prices(raw):
    """What the site's "with prices" filter (refs.FLAG_FILTERS["has_prices"]) selects: goods with prices, or a price
    list shown as photos. The catalog's `has_goods` is the goods alone; `object_flags` names both."""
    flags = as_dict(raw.get("object_flags"))
    return bool(raw.get("has_goods")) or "has_products" in flags or "has_price_list_images" in flags


def _with_scheme(link):
    """'pochta.ru/offices/125009' -> 'https://pochta.ru/offices/125009'; a full link as it is."""
    link = clean(link)
    if not link:
        return None
    if link.startswith("//"):
        return "https:" + link
    return link if re.match(r"^[a-z][a-z0-9+.-]*:", link, re.I) else "https://" + link


def _outbound(link):
    """An outbound link without the site's click counter around it."""
    return _with_scheme(refs.target_of_redirect(link))


def _link_only(value):
    """The value when it is a link (it names its scheme), else None: a fax
    number, a PO box or a messenger handle is not one."""
    return value if isinstance(value, str) and re.match(r"^\s*[a-z][a-z0-9+.-]*:", value, re.I) else None


def _without_params(link, dropped):
    """A link without the query params `dropped(name)` is true for. The
    params that stay are kept as they are written (a query is not always
    UTF-8, and decoding it to write it again would change the link)."""
    parts = urlsplit(link)
    if not parts.query:
        return link
    kept = [pair for pair in parts.query.split("&") if not dropped(unquote_plus(pair.split("=", 1)[0]))]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(kept), parts.fragment))


def _without_prefilled_text(link):
    """A messenger link without the greeting the site pre-fills (?text=...)."""
    return _without_params(link, lambda name: name == "text")


def _without_campaign_tags(link):
    """A website link without the utm_* tags a business adds for its 2GIS card."""
    return _without_params(link, lambda name: name.lower().startswith("utm_"))


def _point(raw):
    point = as_dict(raw.get("point"))
    return number(point.get("lat")), number(point.get("lon"))


def _wkt_point(text):
    """'POINT(49.638854 40.585935)' -> (latitude, longitude); else (None, None)."""
    match = re.match(r"^POINT\(([-0-9.]+) ([-0-9.]+)\)$", text or "")
    return (number(match.group(2)), number(match.group(1))) if match else (None, None)


def _largest_preview(previews):
    """A {size: link} map of one picture -> its full-size link, else its largest preview."""
    previews = as_dict(previews)
    if previews.get("url"):
        return previews["url"]
    sized = []
    for size, link in previews.items():
        match = re.match(r"^(\d+)x(\d*)$", size)
        if match and link:
            sized.append((int(match.group(1)), link))
    return max(sized)[1] if sized else None


# ---- a place -------------------------------------------------------------------------

def parse_categories(raw):
    """The place's categories, its primary one first."""
    rows = [{
        "id": clean(str(r.get("id") or "")),
        "name": clean(r.get("name")),
        "slug": clean(r.get("alias")),
        "is_primary": r.get("kind") == "primary",
        "parent_id": clean(str(r.get("parent_id") or "")),
    } for r in _dicts(raw.get("rubrics"))]
    return sorted(rows, key=lambda r: not r["is_primary"])


def parse_location(raw):
    """Where the place is: the street address in parts, the administrative
    units it lies in, its coordinates."""
    address = as_dict(raw.get("address"))
    components = _dicts(address.get("components"))
    street = next((c for c in components if c.get("type") == "street_number"), {})
    divisions = _dicts(raw.get("adm_div"))

    def division(kind):
        return next((d for d in divisions if d.get("type") == kind), {})

    city, district = division("city"), division("district")
    named = ("country", "region", "city", "district")
    latitude, longitude = _point(raw)
    return {
        "street": clean(street.get("street")),
        "house_number": clean(street.get("number")),
        "building_name": clean(address.get("building_name")),
        "building_id": clean(address.get("building_id")),
        "landmark": clean(address.get("landmark_name")),
        "postcode": clean(address.get("postcode")),
        "makani": clean(address.get("makani")),
        "district": clean(district.get("name")),
        "district_id": clean(district.get("id")),
        "city": clean(city.get("name")),
        "city_id": clean(city.get("id")),
        "city_slug": clean(city.get("city_alias")) or clean(raw.get("city_alias")),
        "region": clean(division("region").get("name")),
        "country": clean(division("country").get("name")),
        "other_divisions": [{"type": clean(d.get("detailed_subtype")) or clean(d.get("type")), "id": clean(d.get("id")),
                             "name": clean(d.get("name"))}
                            for d in divisions if d.get("type") not in named],
        "latitude": latitude,
        "longitude": longitude,
        "timezone_offset_minutes": to_int(raw.get("timezone_offset")),
    }


def parse_contacts(raw):
    """The place's own contacts, in the order the site lists them: phones
    (the number in full: the page prints it after "Show phone"), emails,
    websites and messenger / social links."""
    phones, emails, websites, social = [], [], [], []
    for group in _dicts(raw.get("contact_groups")):
        for contact in _dicts(group.get("contacts")):
            kind = clean(contact.get("type"))
            comment = clean(contact.get("comment"))
            if kind == "phone":
                number_ = clean(contact.get("value")) or clean(contact.get("text"))
                if number_ and all(p["number"] != number_ for p in phones):
                    phones.append({"number": number_, "display": clean(contact.get("text")), "comment": comment})
            elif kind == "email":
                email = clean(contact.get("value")) or clean(contact.get("text"))
                if email and email not in emails:
                    emails.append(email)
            elif kind == "website":
                link = _with_scheme(contact.get("url")) or _outbound(contact.get("value"))
                link = _without_campaign_tags(link) if link else None
                if link and all(w["link"] != link for w in websites):
                    websites.append({"link": link, "text": clean(contact.get("text")), "comment": comment})
            elif kind:
                # a fax, a PO box, a Skype or ICQ handle: a value that is no link is not made into one
                link = _with_scheme(contact.get("url")) or _outbound(_link_only(contact.get("value")))
                if link:
                    link = _without_prefilled_text(link)
                    if all(s["link"] != link for s in social):
                        social.append({"type": kind, "link": link})
    return {
        "website": websites[0]["link"] if websites else None,
        "websites": websites,
        "email": emails[0] if emails else None,
        "emails": emails,
        "phone": phones[0]["number"] if phones else None,
        "phones": phones,
        "social": social,
    }


def parse_opening_hours(schedule):
    """{Mon: {working_hours: [{from, to}]}, ...} -> one entry per weekday,
    Monday first; a day the place is closed has no hours. Times are local,
    HH:MM; "24:00" ends a day and a `to` before its `from` runs past
    midnight, as the site writes them."""
    schedule = as_dict(schedule)
    if not schedule:
        return None
    days = []
    for key, name in refs.DAYS:
        hours = [{"from": clean(h.get("from")), "to": clean(h.get("to"))}
                 for h in _dicts(as_dict(schedule.get(key)).get("working_hours"))]
        days.append({"day": name, "hours": hours})
    return {
        "is_open_24_7": bool(schedule.get("is_24x7")),
        "note": clean(schedule.get("comment")),
        "days": days,
    }


def parse_features(raw):
    """Every attribute the place's card lists (cuisine, average bill,
    payment methods, awards...), each with the group it stands in and the
    tag the search's `attributes` filter takes."""
    features = []
    for group in _dicts(raw.get("attribute_groups")):
        for attribute in _dicts(group.get("attributes")):
            name = clean(attribute.get("name"))
            if name:
                features.append({"group": clean(group.get("name")), "name": name, "tag": clean(attribute.get("tag")),
                                 "is_award": bool(attribute.get("is_award"))})
    return features


def feature_tags(raw):
    """The attribute tags of a raw place (what a filtered search is checked against)."""
    return {a.get("tag") for group in _dicts(raw.get("attribute_groups")) for a in _dicts(group.get("attributes"))}


def offered_tags(result):
    """The attribute tags a raw search result offers as filters (what
    available_filters.attributes lists)."""
    filters = as_dict(as_dict(result).get("filters"))
    return {item.get("tag") for item in _dicts(filters.get("attributes")) if item.get("tag") and clean(item.get("label"))}


def parse_review_sources(raw):
    rows = []
    for item in _dicts(as_dict(raw.get("reviews")).get("items")):
        tag = clean(item.get("tag"))
        rows.append({
            "source": "2gis" if tag == "2gis_reviews" else tag,
            "rating": _rating(item.get("rating")),
            "review_count": _count(item.get("review_count")),
            "recommendation_count": _count(item.get("recommendation_count")),
            "is_reviewable": item.get("is_reviewable") if isinstance(item.get("is_reviewable"), bool) else None,
        })
    return rows


def parse_chain(raw, domain=None, with_rating=False):
    """The organisation the place belongs to (a chain when it has several
    branches). Its own rating is in the data and not on the place's page:
    it is added only when `with_rating` is set."""
    org = as_dict(raw.get("org"))
    if not org.get("id"):
        return None
    chain = {
        "id": clean(org.get("id")),
        "name": clean(org.get("primary")) or clean(org.get("name")),
        "full_name": clean(org.get("name")),
        "name_extension": clean(org.get("extension")),
        "branch_count": _count(org.get("branch_count")),
        "link": refs.branches_link(clean(org.get("id")), domain, clean(raw.get("city_alias"))),
    }
    if with_rating:
        reviews = as_dict(raw.get("reviews"))
        chain.update({"rating": _rating(reviews.get("org_rating")),
                      "review_count": _count(reviews.get("org_review_count")),
                      "rating_count": _count(reviews.get("org_review_count_with_stars"))})
    return chain


def parse_brand(raw):
    brand = as_dict(raw.get("brand"))
    if not brand.get("id"):
        return None
    return {"id": clean(brand.get("id")), "name": clean(brand.get("name")),
            "name_extension": clean(brand.get("extension")), "branch_count": _count(brand.get("branch_count"))}


def _action(action):
    kind = clean(action.get("type"))
    value = clean(action.get("value"))
    return {"caption": clean(action.get("caption")) or clean(action.get("name")), "type": kind,
            "link": _outbound(value) if kind == "link" else None,
            "phone": value if kind == "phone" else None}


def _actions(rows):
    """A card's buttons; the site lists one per platform, so repeats are dropped."""
    actions = []
    for row in _dicts(rows):
        action = _action(row)
        if (action["link"] or action["phone"]) and action not in actions:
            actions.append(action)
    return actions


def parse_promotion(raw, with_legal_name=False):
    """The advertiser's own block on a promoted card: its slogan, the small
    print under it, its button(s), the offers it announces, its logo and
    pictures. None for a place that does not advertise."""
    ads = as_dict(raw.get("ads"))
    options = as_dict(ads.get("options"))
    link = as_dict(ads.get("link"))
    promotion = {
        "text": clean(ads.get("text")),
        "disclaimer": clean(ads.get("text_warning")),
        "link_text": clean(link.get("text")),
        "link": _outbound(link.get("value")),
        "actions": _actions(options.get("actions")),
        "offers": [{"title": clean(s.get("title")), "discount_percent": _count(s.get("percent")),
                    "image": clean(s.get("preview_image_url"))}
                   for s in _dicts(g(options, "organization_stories", "previews", default=None)) if clean(s.get("title"))],
        "logo": clean(g(options, "logo", "img_url", default=None)),
        "images": [clean(i.get("url")) for i in _dicts(options.get("images")) if i.get("url")],
    }
    if with_legal_name:
        promotion["advertiser_legal_name"] = clean(options.get("legal_name"))
    if not any(value for value in promotion.values()):
        return None
    return promotion


def parse_order_links(raw):
    """The partner services the card offers an order or a booking through
    (a delivery service, a table booking), each with its button."""
    rows = []
    for partner in _dicts(g(raw, "ads", "options", "buy_here", default=None)):
        for parsed in _actions(partner.get("actions")):
            if parsed:
                rows.append({"partner": clean(partner.get("title")), "partner_id": clean(partner.get("org_id")),
                             **parsed, "disclaimer": clean(g(partner, "ads_texts", "text_warning", default=None))})
    return rows


def parse_stations(raw):
    return [{
        "id": clean(s.get("id")),
        "name": clean(s.get("name")),
        "line": clean(s.get("comment")),
        "distance_meters": _count(s.get("distance")),
        "transport_types": [clean(t) for t in as_list(s.get("route_types")) if clean(t)],
        "color": clean(s.get("color")),
    } for s in _dicts(g(raw, "links", "nearest_stations", default=None))]


def parse_parking(raw):
    return [{
        "id": clean(p.get("id")),
        "name": clean(p.get("name")),
        "capacity": _count(p.get("capacity")),
        "is_paid": p.get("is_paid") if isinstance(p.get("is_paid"), bool) else None,
        "purpose": clean(p.get("purpose")),
        "has_ev_charging": bool(p.get("for_emobile")),
    } for p in _dicts(g(raw, "links", "parking", default=None))]


def parse_landmarks(raw):
    return [{
        "id": clean(m.get("id")),
        "name": clean(m.get("name")),
        "type": clean(m.get("subtype")) or clean(m.get("type")),
        "distance_meters": _count(m.get("distance")),
        "bearing_degrees": _count(m.get("azimuth")),
    } for m in _dicts(g(raw, "links", "landmarks", default=None))]


def parse_building(raw):
    """What the catalog says of the building itself (a record of the kind
    `building`, and a place that fills a whole one): its purpose, floors,
    structure, how many businesses and internet providers it holds and the
    services assigned to it (its post office)."""
    structure = as_dict(raw.get("structure_info"))
    links = as_dict(raw.get("links"))
    services = []
    for item in _dicts(g(links, "servicing", "items", default=None)):
        contact = next((c for c in _dicts(item.get("contacts")) if c.get("type") == "website"), {})
        services.append({"name": clean(item.get("name")), "kind": clean(item.get("additional_info")),
                         "chain_id": clean(item.get("org_id")), "place_id": place_id(item.get("id")),
                         "website": _with_scheme(contact.get("url") or contact.get("value"))})
    building = {
        "purpose": clean(raw.get("purpose_name")),
        "floor_count": _count(g(raw, "floors", "ground_count", default=None)),
        "material": clean(structure.get("material")),
        "floor_type": clean(structure.get("floor_type")),
        "gas_type": clean(structure.get("gas_type")),
        "project_type": clean(structure.get("project_type")),
        "year_built": _count(structure.get("year_of_construction")),
        "elevator_count": _count(structure.get("elevators_count")),
        "is_in_emergency_state": (structure.get("is_in_emergency_state")
                                  if isinstance(structure.get("is_in_emergency_state"), bool) else None),
        "business_count": _count(g(links, "branches", "count", default=None)),
        "internet_provider_count": _count(g(links, "providers", "count", default=None)),
        "services": services,
    }
    if not any(value for value in building.values()):
        return None
    return building


def parse_place(raw, domain=None, chain_rating=False, advertiser_legal_name=False):
    """One catalog record (a search row or a record asked by id) -> the
    place. `domain` is the country site of the place's region ("ru", "ae"):
    with it the link is the page itself, without it the 2gis.com link the
    site redirects there."""
    if not isinstance(raw, dict):
        return None
    name_ex = as_dict(raw.get("name_ex"))
    reviews = as_dict(raw.get("reviews"))
    kind = clean(raw.get("type"))
    ident = place_id(raw.get("id"))
    categories = parse_categories(raw)
    albums = [a for a in _dicts(raw.get("external_content")) if a.get("type") == "photo_album"]
    common = next((a for a in albums if a.get("subtype") == "common"), albums[0] if albums else {})
    dates = as_dict(raw.get("dates"))
    return {
        "id": ident,
        "name": clean(name_ex.get("primary")) or clean(raw.get("name")),
        "full_name": clean(raw.get("name")),
        "name_extension": clean(name_ex.get("extension")),
        "link": refs.place_link(ident, domain, clean(raw.get("city_alias")), kind),
        "type": kind,
        "subtype": clean(raw.get("subtype")),
        "summary": multiline(g(raw, "summary", "text", default=None)),
        "description": multiline(raw.get("description")),
        "category": categories[0]["name"] if categories else None,
        "categories": categories,
        "poi_category": clean(raw.get("poi_category")),
        "rating": _rating(reviews.get("general_rating")),
        "review_count": _count(reviews.get("general_review_count")),
        "rating_count": _count(reviews.get("general_review_count_with_stars")),
        "review_sources": parse_review_sources(raw),
        "address": clean(raw.get("address_name")),
        "address_comment": clean(raw.get("address_comment")),
        "full_address": clean(raw.get("full_name")),
        "location": parse_location(raw),
        **parse_contacts(raw),
        "opening_hours": parse_opening_hours(raw.get("schedule")),
        "chain": parse_chain(raw, domain, chain_rating),
        "brand": parse_brand(raw),
        "features": parse_features(raw),
        "highlights": [clean(f.get("name")) for f in _dicts(g(raw, "context", "stop_factors", default=None))
                       if clean(f.get("name"))],
        "promotion": parse_promotion(raw, advertiser_legal_name),
        "order_links": parse_order_links(raw),
        "photo_count": _count(common.get("count")),
        "main_photo": clean(common.get("main_photo_url")),
        "nearest_stations": parse_stations(raw),
        "landmarks": parse_landmarks(raw),
        "parking": parse_parking(raw),
        "building": parse_building(raw),
        "has_photos": bool(g(raw, "flags", "photos", default=False)),
        "has_prices": lists_prices(raw),
        "has_discount": bool(raw.get("has_discount")),
        "is_promoted": bool(raw.get("is_promoted")),
        "is_advertised": bool(g(raw, "stat", "is_advertised", default=False) or g(raw, "ads", "options", "advertiser", default=False)),
        "language": clean(raw.get("locale")),
        "created_at": iso_utc(dates.get("created_at")),
        "updated_at": iso_utc(dates.get("updated_at")),
    }


def parse_places(rows, domains=None, **options):
    """Raw catalog rows -> places. `domains` maps a region id to its country site."""
    domains = domains or {}
    places = []
    for raw in _dicts(rows):
        place = parse_place(raw, domains.get(str(raw.get("region_id"))), **options)
        if place and place["id"]:
            places.append(place)
    return places


# ---- a search page -------------------------------------------------------------------

def parse_available_filters(result):
    """What this result can be narrowed by: its categories, the cities and
    districts it spans, and the attribute tags the `attributes` param
    takes (a range attribute with the span the site offers)."""
    filters = as_dict(result.get("filters"))
    spatial = as_dict(filters.get("spatial"))
    attributes = []
    for item in _dicts(filters.get("attributes")):
        name = clean(item.get("label"))
        if not name or not item.get("tag"):
            continue        # the site's own controls (sort, worktime, district...): they have params of their own
        span = as_dict(item.get("range"))
        attributes.append({"tag": clean(item.get("tag")), "name": name, "type": clean(item.get("type")),
                           "min": number(span.get("min")), "max": number(span.get("max"))})
    return {
        "categories": [{"id": clean(str(r.get("id") or "")), "name": clean(r.get("name")), "count": _count(r.get("count"))}
                       for r in _dicts(filters.get("rubrics"))],
        "cities": [{"id": clean(c.get("id")), "name": clean(c.get("name")), "count": _count(c.get("count"))}
                   for c in _dicts(spatial.get("cities"))],
        "districts": [{"id": clean(d.get("id")), "name": clean(d.get("name")), "city_id": clean(d.get("city_id"))}
                      for d in _dicts(spatial.get("districts"))],
        "attributes": attributes,
    }


def pagination(page, per_page, total, max_rows=None):
    """The `pagination` block. `max_rows` is how deep the site serves the
    list: total_pages stops there, total_count stays what the site counts."""
    total = _count(total) or 0
    served = min(total, max_rows) if max_rows else total
    return {"page": page, "items_per_page": per_page,
            "total_pages": (served + per_page - 1) // per_page if per_page else 0, "total_count": total}


def parse_search(result, page, limit, domains=None, **options):
    """The catalog's `result` of a search -> {pagination, places,
    available_filters}. `result` None is a page with nothing on it."""
    result = as_dict(result)
    return {
        "pagination": pagination(page, limit, result.get("total"), refs.MAX_SEARCH_ROWS),
        "places": parse_places(result.get("items"), domains, **options),
        "available_filters": parse_available_filters(result),
    }


def parse_city_match(rows):
    """The city lookup's rows -> the city or town the site means:
    {city_id, name, slug, region_id}. Only its first row counts (the lookup
    knows a city by its other names too: "Питер", "Алма-Ата"), and only
    when the site covers the place: a town it merely knows the name of
    lies in region 0, and so does the junk it answers for a city it does
    not have ("New York" -> a village called "New"). None otherwise."""
    rows = _dicts(rows)
    if not rows:
        return None
    raw = rows[0]
    region = clean(str(raw.get("region_id") or ""))
    if raw.get("type") != "adm_div" or not region or region == "0":
        return None
    return {"city_id": place_id(raw.get("id")), "name": clean(raw.get("name")),
            "slug": clean(raw.get("city_alias")), "region_id": region}


# ---- reviews -------------------------------------------------------------------------

def _review_author(user):
    user = as_dict(user)
    if not user:
        return None
    public_id = clean(user.get("public_id"))
    return {
        "name": clean(user.get("name")),
        "first_name": clean(user.get("first_name")),
        "last_name": clean(user.get("last_name")),
        "public_id": public_id,
        "link": refs.user_link(public_id) or _with_scheme(user.get("url")),
        "photo": _largest_preview(user.get("photo_preview_urls")),
        "review_count": _count(user.get("reviews_count")),
        "source": clean(user.get("provider")),
    }


def parse_review(raw):
    trust = as_dict(raw.get("trust_factors"))
    visit = next((f for f in _dicts(trust.get("factors")) if f.get("type") == "location_visit"), {})
    answer = as_dict(raw.get("official_answer"))
    photos = [_largest_preview(g(m, "photo", "preview_urls", default=None)) for m in _dicts(raw.get("media"))]
    return {
        "id": clean(str(raw.get("id") or "")),
        "rating": _count(raw.get("rating")) or None,        # a review without stars (Foursquare's) is sent as 0
        "text": multiline(raw.get("text")),
        "source": clean(raw.get("provider")),
        "source_link": _with_scheme(raw.get("url")),
        "created_at": iso_utc(raw.get("date_created")),
        "edited_at": iso_utc(raw.get("date_edited")),
        "likes_count": _count(raw.get("likes_count")),
        "comments_count": _count(raw.get("comments_count")),
        "is_confirmed": bool(raw.get("is_rated")),
        "is_hidden": bool(raw.get("is_hidden")),
        "is_pinned": g(raw, "additional_data", "pin_status", default=None) not in (None, "", "none"),
        "hidden_reason": clean(raw.get("hiding_reason")),
        "visit_count": _count(visit.get("visits_count")),
        "trust_note": clean(g(trust, "trust_info", "confirmed_caption", default=None)),
        "place_id": clean(g(raw, "object", "id", default=None)),
        "author": _review_author(raw.get("user")),
        "photos": [p for p in photos if p],
        "reactions": [{"type": clean(e.get("id")), "count": _count(e.get("count"))}
                      for e in _dicts(raw.get("emojis")) if e.get("id")],
        "owner_reply": {
            "id": clean(str(answer.get("id") or "")),
            "text": multiline(answer.get("text")),
            "created_at": iso_utc(answer.get("date_created")),
            "organization": clean(answer.get("org_name")),
            "logo": _largest_preview(answer.get("logo_preview_urls")),
        } if answer else None,
    }


def is_known_place(payload):
    """The reviews API answers an unknown place with HTTP 200 and no review
    sources; every real place lists the sources a review may come from."""
    return bool(as_list(g(payload, "meta", "providers", default=None)))


def review_sources(payload):
    """The review sources of the place's market: the reviews API names them in every answer."""
    return [clean(p.get("tag")) for p in _dicts(g(payload, "meta", "providers", default=None)) if p.get("tag")]


def without_reviews(payload):
    """The same answer with no review selected; the place's own totals stay."""
    return {**payload, "meta": {**as_dict(payload.get("meta")), "total_count": 0}, "reviews": []}


def parse_reviews(payload, page, limit):
    """One page of the reviews API -> {place totals, pagination, reviews}.
    `total_count` counts the reviews the call's filters select."""
    meta = as_dict(payload.get("meta"))
    return {
        "rating": _rating(meta.get("branch_rating")),
        "is_rating_confirmed": bool(meta.get("branch_rating_is_confirmed")),
        "review_count": _count(meta.get("branch_reviews_count")),
        "sources": review_sources(payload),
        "pagination": pagination(page, limit, meta.get("total_count")),
        "reviews": [parse_review(r) for r in _dicts(payload.get("reviews"))],
    }


def parse_review_topics(payload):
    """The topics a place's reviews mention, the pinned ones marked."""
    return [{"name": clean(f.get("value")), "is_pinned": bool(g(f, "additional_data", "is_pinned", default=False))}
            for f in _dicts(as_dict(payload).get("facts")) if clean(f.get("value"))]


def parse_rating_counts(payload):
    """{"ratings": {"1": 130, ...}} -> [{rating: 5, count: 1274}, ...], best first."""
    counts = as_dict(as_dict(payload).get("ratings"))
    rows = [{"rating": to_int(stars), "count": _count(count)} for stars, count in counts.items() if to_int(stars)]
    return sorted(rows, key=lambda r: -r["rating"])


def parse_review_summary(payload):
    return multiline(as_dict(payload).get("text"))


# ---- photos --------------------------------------------------------------------------

def parse_photo(raw):
    kind = clean(raw.get("media_type"))
    photo, video = as_dict(raw.get("photo")), as_dict(raw.get("video"))
    owner, source = as_dict(raw.get("owner")), as_dict(raw.get("copyright"))
    public_id = clean(owner.get("public_id"))
    streams = [{"type": clean(u.get("type")), "link": clean(u.get("url"))} for u in _dicts(video.get("urls")) if u.get("url")]
    return {
        "id": clean(str(raw.get("id") or "")),
        "type": kind,
        "link": clean(photo.get("url")) or (streams[0]["link"] if streams else None),
        "thumbnail": _largest_preview(photo.get("preview_urls")) or _largest_preview(video.get("cover_urls")),
        "width": _count(photo.get("width") or video.get("width")),
        "height": _count(photo.get("height") or video.get("height")),
        "duration_seconds": _count(video.get("duration_sec")),
        "video_streams": streams,
        "keywords": [clean(k) for k in as_list(raw.get("content_key_words")) if clean(k)],
        "source": clean(source.get("code")),
        "source_note": clean(source.get("title")) if source.get("type") == "image" or not owner else clean(source.get("value")),
        "source_link": clean(source.get("url")),
        "author": {"name": clean(owner.get("name")), "public_id": public_id, "link": refs.user_link(public_id),
                   "photo": clean(owner.get("avatar")) if isinstance(owner.get("avatar"), str) else None} if owner else None,
        "reactions": [{"type": clean(e.get("id")), "count": _count(e.get("count"))}
                      for e in _dicts(raw.get("emojis")) if e.get("id")],
        "is_pinned": bool(raw.get("is_pinned")),
        "created_at": iso_utc(raw.get("creation_time")),
        "updated_at": iso_utc(raw.get("modification_time")),
    }


def parse_photos(payload):
    """One page of the photo API -> {albums, photos, next_cursor}."""
    return {
        "albums": [{"id": clean(a.get("id")), "name": clean(a.get("name")), "count": _count(a.get("count"))}
                   for a in _dicts(payload.get("albums"))],
        "photos": [parse_photo(p) for p in _dicts(payload.get("items"))],
        "next_cursor": clean(payload.get("next_page_token")),
    }


# ---- suggestions ---------------------------------------------------------------------

_SUGGESTION_KINDS = {"rubric": "category", "metarubric": "category_group", "meta_rubric": "category_group",
                     "org": "chain", "branch": "place", "user_query": "query", "attribute": "filter", "brand": "brand"}


def parse_suggestion(raw, domain=None):
    """One row of the suggestion box. `text` is what the box completes the
    typed text to; a category, a chain, a place or a filter also carries
    its id, and a place its address and link."""
    kind = clean(raw.get("type"))
    extend = as_dict(raw.get("extend"))
    ident = place_id(str(raw.get("id") or ""))
    latitude, longitude = _point(raw)
    is_place = kind == "branch"
    is_chain = kind == "org"
    city_slug = clean(raw.get("city_alias"))
    link = None
    if is_place:
        link = refs.place_link(ident, domain, city_slug)
    elif is_chain:
        link = refs.branches_link(clean(g(raw, "org", "id", default=None)) or ident, domain, city_slug)
    return {
        "type": _SUGGESTION_KINDS.get(kind, kind),
        "text": clean(g(raw, "search_attributes", "suggested_text", default=None)),
        "id": ident,
        "name": clean(raw.get("name")) or clean(extend.get("search_name")),
        "group": clean(extend.get("category_label")),
        "place_count": _count(next((v for v in (raw.get("branch_count"), extend.get("objects_count"),
                                                g(raw, "org", "branch_count", default=None) if is_chain else None)
                                    if v is not None), None)),
        "address": clean(raw.get("address_name")),
        "rating": _rating(g(raw, "reviews", "general_rating", default=None)),
        "review_count": _count(g(raw, "reviews", "general_review_count", default=None)),
        "category": next((c["name"] for c in parse_categories(raw)), None),
        "chain_id": clean(g(raw, "org", "id", default=None)),
        "latitude": latitude,
        "longitude": longitude,
        "link": link,
    }


def parse_suggestions(rows, domain=None):
    return [parse_suggestion(r, domain) for r in _dicts(rows)]


# ---- cities and categories -----------------------------------------------------------

_REGION_FEATURES = (("metro", "metro"), ("public_transport", "public_transport"), ("traffic", "traffic"),
                    ("parking_layer", "parking"), ("road_network", "road_network"), ("2gis_reviews", "reviews"),
                    ("flamp", "flamp_reviews"))


def parse_city(raw):
    """One region of the site: a city with its surroundings, or a small
    country (the site's "dubai" is the whole UAE). `slug` is what the
    `city` param takes and what the site's links start with."""
    slug = clean(raw.get("uri_code")) or clean(raw.get("code"))
    domain = clean(raw.get("domain"))
    stats = as_dict(raw.get("statistics"))
    position = as_dict(raw.get("default_pos"))
    flags = as_dict(raw.get("flags"))
    towns = []
    for town in _dicts(raw.get("satellites")):
        latitude, longitude = _wkt_point(town.get("centroid"))
        if clean(town.get("name")):
            towns.append({"name": clean(town.get("name")), "latitude": latitude, "longitude": longitude})
    return {
        "id": clean(str(raw.get("id") or "")),
        "slug": slug,
        "name": clean(raw.get("name")),
        "link": refs.city_link(domain, slug),
        "other_slugs": [clean(a) for a in as_list(raw.get("uri_aliases")) if clean(a) and clean(a) != slug],
        "country_code": (clean(raw.get("country_code")) or "").upper() or None,
        "site": f"2gis.{domain}" if domain else None,
        "language": clean(raw.get("locale")),
        "languages": [clean(v) for v in as_list(raw.get("locales")) if clean(v)],
        "timezone": clean(g(raw, "time_zone", "name", default=None)),
        "timezone_offset_minutes": to_int(g(raw, "time_zone", "offset", default=None)),
        "latitude": number(position.get("lat")),
        "longitude": number(position.get("lon")),
        "place_count": _count(stats.get("branch_count")),
        "company_count": _count(stats.get("org_count")),
        "category_count": _count(stats.get("rubric_count")),
        "transport_route_count": _count(stats.get("route_count")),
        "features": [name for key, name in _REGION_FEATURES if flags.get(key)],
        "towns": towns,
    }


def parse_cities(rows):
    return [city for city in (parse_city(r) for r in _dicts(rows)) if city["id"] and city["slug"]]


def _category(raw, domain, city_slug, with_link=True):
    ident = clean(str(raw.get("id") or ""))
    slug = clean(raw.get("alias"))
    category = {"id": ident, "name": clean(raw.get("name")), "slug": slug}
    if with_link:
        category["link"] = refs.category_link(domain, city_slug, slug or clean(raw.get("name")), ident)
    category.update({"place_count": _count(raw.get("branch_count")), "company_count": _count(raw.get("org_count"))})
    return category


def parse_categories_tree(rows, domain=None, city_slug=None):
    """A region's category tree: the top-level groups, each with its
    categories. A category's `id` is what the search's `category_id` takes;
    a group is a heading of the site's catalog and has no page of its own."""
    tree = []
    for group in _dicts(rows):
        tree.append({**_category(group, domain, city_slug, with_link=False),
                     "subcategories": [_category(r, domain, city_slug) for r in _dicts(group.get("rubrics"))]})
    return tree
