"""`route_glue` is the hosted service's routing layer and is not part of this repo: routes.py does its job here.
One test drives it directly, so that test is skipped; every other test in its file runs."""
import sys
import types

import pytest

sys.modules.setdefault("route_glue", types.ModuleType("route_glue"))

NEEDS_THE_HOSTED_ROUTE_LAYER = {"test_search_pages_stop_at_the_depth_the_site_serves"}


def pytest_collection_modifyitems(items):
    for item in items:
        if item.name in NEEDS_THE_HOSTED_ROUTE_LAYER:
            item.add_marker(pytest.mark.skip(reason="drives the hosted service's route layer"))
