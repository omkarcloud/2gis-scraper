"""Use the scraper straight from Python, no server needed.

    python main.py

Each call validates its params with the same schema the API uses (so the
defaults apply) and returns the same JSON the API does, except that a list
keeps its `pagination` block instead of next / previous links. The results
are written to output/*.json.
"""
import json
import os

from two_gis import api, schemas
from schema_fields import load_query

os.makedirs("output", exist_ok=True)


def run(schema_cls, impl, **params):
    data, error = load_query(schema_cls, {k: str(v) for k, v in params.items()})
    if error:
        raise ValueError(error)
    return impl(**data)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


if __name__ == "__main__":
    # a full business profile: Atlantis The Palm Dubai with phones, website, socials, hours, rating and features
    save("places_details.json", run(schemas.PlaceDetailsSchema, api.places_details, id='70000001017274587'))

    # what 2GIS suggests as you type "starb" in Dubai: categories, chains, places and popular searches
    save("places_suggestions.json", run(schemas.PlacesSuggestionsSchema, api.places_suggestions, query='starb', city='Dubai'))

    # every Starbucks in the UAE, 20 per page, with phones, website, rating and hours
    save("places_search.json", run(schemas.PlacesSearchSchema, api.places_search, query='Starbucks', city='Dubai'))
