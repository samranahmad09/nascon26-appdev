"""Appium 2 + Selenium Grid fixtures for the native-app probes (Section 6.3).

Run from the repo root (the phone on USB, Appium on :4723, Grid on :4444):
    pytest tests/appium --confcutdir=tests/appium --junitxml=tests/reports/appium-report.xml
"""
import os
import subprocess
import time

import pytest
import requests

APPIUM_URL = os.environ.get("APPIUM_URL", "http://localhost:4723")
GRID_URL = os.environ.get("GRID_URL", "http://localhost:4444")
API_URL = os.environ.get("API_URL", "http://localhost:8080")  # backend as seen from the test runner
BASE_URL = os.environ.get("BASE_URL", "http://backend:8080")  # backend as seen from the Grid browser
DEVICE = os.environ.get("ANDROID_SERIAL", "R5CY5221FEV")
APP_PACKAGE = "com.nascon26.nascon_league"
APP_ACTIVITY = ".MainActivity"
COSTREAM = {
    "session_id": "cs-session-101",
    "host_player_id": "player-alpha-1",
    "participants": ["player-alpha-1", "player-alpha-2", "player-beta-1", "player-beta-2"],
    "layout": "grid_4",
    "simulate_host_drop": False,
}


def adb(*args, check=False):
    return subprocess.run(["adb", "-s", DEVICE, *args], capture_output=True, text=True, check=check)


@pytest.fixture(scope="session")
def appium_ready():
    r = requests.get(f"{APPIUM_URL}/status", timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    ready = body.get("value", {}).get("ready", body.get("ready"))
    assert ready is True, body
    return body


@pytest.fixture(scope="session")
def phone_reaches_backend():
    # The phone talks to the laptop backend over USB.
    adb("reverse", "tcp:8080", "tcp:8080")
    adb("reverse", "tcp:8888", "tcp:8888")
    return True


@pytest.fixture(scope="session")
def costream_session():
    """Make sure cs-session-101 exists (idempotent replay returns 200)."""
    r = requests.post(f"{API_URL}/audit/v1/costream/session", json=COSTREAM, timeout=10)
    if r.status_code == 404 and "COSTREAM" not in r.text and "PLAYER" not in r.text:
        # Route not deployed on this backend build: the app still opens the screen and the chat room.
        return None
    assert r.status_code in (200, 201), r.text
    return r.json()


@pytest.fixture(scope="session")
def driver(appium_ready, phone_reaches_backend):
    from appium import webdriver
    from appium.options.android import UiAutomator2Options

    opts = UiAutomator2Options()
    opts.platform_name = "Android"
    opts.automation_name = "UiAutomator2"
    opts.udid = DEVICE
    opts.app_package = APP_PACKAGE
    opts.app_activity = APP_ACTIVITY
    opts.no_reset = True
    opts.new_command_timeout = 300
    opts.set_capability("appium:autoGrantPermissions", True)
    drv = webdriver.Remote(APPIUM_URL, options=opts)
    drv.implicitly_wait(0)
    yield drv
    drv.quit()


def fire_deep_link(uri):
    """Same command the desk audit runs."""
    return adb("shell", "am", "start", "-W", "-a", "android.intent.action.VIEW", "-d", uri)


def wait_for_id(driver, ident, timeout):
    """Flutter Semantics(identifier) shows up as the node's resource-id."""
    from appium.webdriver.common.appiumby import AppiumBy

    deadline = time.monotonic() + timeout
    selector = f'new UiSelector().resourceIdMatches(".*{ident}$")'
    while time.monotonic() < deadline:
        found = driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, selector)
        if found:
            return found[0]
        time.sleep(0.1)
    return None


def wait_for_text(driver, text, timeout):
    from appium.webdriver.common.appiumby import AppiumBy

    deadline = time.monotonic() + timeout
    xpath = f'//*[contains(@content-desc, "{text}") or contains(@text, "{text}")]'
    while time.monotonic() < deadline:
        if driver.find_elements(AppiumBy.XPATH, xpath):
            return True
        time.sleep(0.1)
    return False


def current_package(driver):
    try:
        return driver.current_package
    except Exception:
        return None
