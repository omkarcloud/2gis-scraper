"""Configuration for the 2GIS Scraper. Everything can be set with an environment
variable; the defaults work out of the box.

    PORT            port the API listens on (default 8000)
    TWO_GIS_PROXY   proxy URL for every request, e.g. http://user:pass@host:port
                    (default: none, requests go out from your own connection)

No proxy needed: 2GIS answers from your own connection.

Everything else below is a plain constant with a working default.
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

TWO_GIS_PROXY = os.environ.get("TWO_GIS_PROXY") or None


# ---- what the code reads off config: defaults for a direct run ----
MAX_RETRIES = 3
RETRY_BACKOFF = 2
TWO_GIS_ADVERTISER_LEGAL_NAME = False
TWO_GIS_CHAIN_RATING = True
TWO_GIS_EXIT = TWO_GIS_PROXY      # the exit every session leaves through: your proxy, or direct
TWO_GIS_KEY_REFRESH_INTERVAL = 600
TWO_GIS_SIGN_SALT = 'baf4c54e9dae'

# The public keys 2GIS's own web pages send: every visitor's browser gets them in the page's config.
# When the site changes them, two_gis/fetch.py reads the new ones from its page by itself.
TWO_GIS_CATALOG_KEY = "c7f1a769-c8a5-4636-b14d-d8c987808a12"
TWO_GIS_REVIEWS_KEY = "6e7e1929-4ea9-4a5d-8c05-d601860389bd"
TWO_GIS_PHOTOS_KEY = "gYu1s9N1wP"
