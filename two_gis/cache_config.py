"""Cache TTL per /2gis/* surface (cache.py, keyed on the validated params).

A place's record carries its own `updated_at`: the cards seen on
2026-11-21 were last edited weeks to months earlier, and new places enter
a city's list a few a day.
"""
from datetime import timedelta

# places/search and places/branches: the ranking and the "open now" filter
# move through the day, new places and edits by the day
SEARCH_CACHE = timedelta(hours=6)
# places/details and places/batch: a card is edited every few weeks; its
# rating and review count move with every new review
PLACE_CACHE = timedelta(hours=12)
# places/reviews: a busy place gains several reviews a day, and the first
# page is the newest ones
REVIEWS_CACHE = timedelta(hours=3)
# places/photos: a few new photos a week on a busy place; a page's cursor
# is valid for seven days
PHOTOS_CACHE = timedelta(hours=12)
# places/suggestions: the suggestion index follows the catalog
SUGGESTIONS_CACHE = timedelta(hours=24)
# cities/list and the region table every endpoint reads: a region is added
# a few times a year; its place counts drift by the day
CITIES_CACHE = timedelta(hours=24)
# a city name -> its region and city id: fixed for a name
CITY_LOOKUP_CACHE = timedelta(days=7)
# categories/list: the rubric tree changes with the site's releases; its
# place counts drift by the day
CATEGORIES_CACHE = timedelta(hours=24)

# seconds the region table is kept in the process between reads of the
# shared cache (every request needs it for links and languages)
REGION_TABLE_MEMO_SECONDS = 3600
