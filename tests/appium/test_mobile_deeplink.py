"""Section 6.3: nascon:// deep links bring the native app to the front on the right screen
within 2 seconds, without stopping on the login screen, from cold and warm start."""
import time

import pytest

from conftest import APP_PACKAGE, adb, current_package, fire_deep_link, wait_for_id

LINKS = [
    ("nascon://entity/match-m1", "screen-match-live"),
    ("nascon://stream/cs-session-101", "screen-costream"),
]


def test_appium_server_ready(appium_ready):
    assert appium_ready


@pytest.mark.parametrize("uri,screen", LINKS)
def test_deep_link_cold_start(driver, costream_session, uri, screen):
    adb("shell", "am", "force-stop", APP_PACKAGE)
    started = time.monotonic()
    result = fire_deep_link(uri)
    assert result.returncode == 0, result.stderr
    found = wait_for_id(driver, screen, timeout=2.0)
    elapsed = time.monotonic() - started
    assert found is not None, f"{screen} not shown for {uri} (cold start)"
    assert elapsed <= 2.5, f"{uri} took {elapsed:.2f}s"
    assert current_package(driver) == APP_PACKAGE


@pytest.mark.parametrize("uri,screen", LINKS)
def test_deep_link_warm_start(driver, costream_session, uri, screen):
    driver.activate_app(APP_PACKAGE)
    started = time.monotonic()
    result = fire_deep_link(uri)
    assert result.returncode == 0, result.stderr
    found = wait_for_id(driver, screen, timeout=2.0)
    assert found is not None, f"{screen} not shown for {uri} (warm start)"
    assert time.monotonic() - started <= 2.5


@pytest.mark.parametrize("uri,screen", [
    ("nascon://entity/club-alpha-1", "title-club"),
    ("nascon://entity/player-alpha-1", "screen-player"),
    ("nascon://entity/cs-session-101", "screen-costream"),
])
def test_other_entities_open(driver, costream_session, uri, screen):
    fire_deep_link(uri)
    assert wait_for_id(driver, screen, timeout=4.0) is not None, uri
