"""Selenium Grid 4 harness for the Guest Web Player (6.2.3). Every test opens its own remote browser and needs
nothing from another test, so the suite runs in parallel: `pytest -n 3 tests/selenium/`.

GRID_URL           Selenium hub (default http://localhost:4444, else http://selenium-hub:4444 inside compose)
BASE_URL           backend as the browser containers reach it (default http://backend:8080, the compose alias)
API_URL            backend as this test process reaches it (default http://localhost:8080, else http://backend:8080)
SELENIUM_BROWSERS  browsers each test runs in (default chrome,firefox)

The suite never resets the backend; it expects the /audit/v1/reset baseline (match-m1 in progress).
"""
from pathlib import Path

import pytest
from selenium import webdriver

from grid import API_URL, BROWSERS, GRID_URL, api

REPORTS = Path(__file__).resolve().parents[1] / "reports"


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    """JUnit XML (and HTML when pytest-html is installed) go to tests/reports/ unless the caller chose a path."""
    REPORTS.mkdir(parents=True, exist_ok=True)
    if not getattr(config.option, "xmlpath", None):
        config.option.xmlpath = str(REPORTS / "selenium-report.xml")
    if config.pluginmanager.hasplugin("html") and not getattr(config.option, "htmlpath", None):
        config.option.htmlpath = str(REPORTS / "selenium-report.html")
        config.option.self_contained_html = True


def pytest_generate_tests(metafunc):
    if "browser_name" in metafunc.fixturenames:
        metafunc.parametrize("browser_name", BROWSERS)


def _options(name: str):
    if name == "firefox":
        opts = webdriver.FirefoxOptions()
        opts.add_argument("-headless")
        opts.set_preference("media.autoplay.default", 0)
        return opts
    opts = webdriver.ChromeOptions()
    for arg in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                "--autoplay-policy=no-user-gesture-required", "--window-size=1280,900"):
        opts.add_argument(arg)
    return opts


@pytest.fixture
def driver(browser_name):
    drv = webdriver.Remote(command_executor=GRID_URL, options=_options(browser_name))
    try:
        drv.set_window_size(1280, 900)
        drv.set_page_load_timeout(30)
        yield drv
    finally:
        drv.quit()


@pytest.fixture(scope="session")
def live_match():
    status, score = api("GET", "/audit/v1/score/match-m1")
    if status != 200:
        pytest.fail(f"backend at {API_URL} has no match-m1 (GET /audit/v1/score/match-m1 -> {status}); "
                    "run POST /audit/v1/reset once before the suite")
    return score
