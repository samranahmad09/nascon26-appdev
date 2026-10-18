"""Guest Web Player on Selenium Grid 4 (6.2.3 tests 1-3, plus the NFR-8 DOM inspection). Each test starts a fresh
browser, so each one is a fresh guest; run in parallel with `pytest -n 3 tests/selenium/`."""
import os
import re
import time
import uuid
from urllib.parse import urlparse

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from grid import BASE_URL, api, raw_redirect

LIVE_PATH = "/web/matches/match-m1/live"
# Demo member seeded by /audit/v1/reset (app/core/seed_data.json); override for other environments.
MEMBER_EMAIL = os.environ.get("MEMBER_EMAIL", "viewer@nascon.test")
MEMBER_PASSWORD = os.environ.get("MEMBER_PASSWORD", "Nascon@26")
TESTIDS = {"guest-video-player", "live-score-banner", "pinned-merch-card", "add-to-guest-cart-btn", "guest-cart-count",
           "chat-message-list", "chat-input-field", "chat-send-btn", "progressive-auth-modal"}
SCORE = re.compile(r"^(\d+) - (\d+)$")


def tid(name: str):
    return By.CSS_SELECTOR, f'[data-testid="{name}"]'


def body_flag(driver, name: str):
    return driver.execute_script("return document.body.getAttribute(arguments[0]);", f"data-{name}")


def open_live(driver):
    driver.get(BASE_URL + LIVE_PATH)
    return WebDriverWait(driver, 10)


def test_1_qr_resolves_to_live_player_without_login(driver, live_match):
    status, location = raw_redirect("/q/m1-live")
    assert status == 302, f"GET /q/m1-live -> {status}"
    assert urlparse(location).path == LIVE_PATH

    started = time.monotonic()
    driver.get(BASE_URL + "/q/m1-live")
    wait = WebDriverWait(driver, 3)
    wait.until(lambda d: urlparse(d.current_url).path == LIVE_PATH)
    player = wait.until(EC.visibility_of_element_located(tid("guest-video-player")))
    banner = wait.until(EC.visibility_of_element_located(tid("live-score-banner")))
    elapsed = time.monotonic() - started
    assert elapsed < 3.0, f"player and banner took {elapsed:.2f}s"
    assert player.size["width"] > 0 and player.size["height"] > 0
    assert SCORE.match(banner.text.strip()), banner.text

    modal = driver.find_elements(*tid("progressive-auth-modal"))
    assert not any(m.is_displayed() for m in modal), "login modal visible on load"
    found = driver.execute_script(
        "return Array.from(document.querySelectorAll('[data-testid]')).map(e => e.getAttribute('data-testid'));")
    assert sorted(found) == sorted(TESTIDS)


def test_2_guest_chat_send_is_blocked_and_opens_auth_modal(driver, live_match):
    wait = open_live(driver)
    field = wait.until(EC.element_to_be_clickable(tid("chat-input-field")))
    assert not driver.find_element(*tid("progressive-auth-modal")).is_displayed()
    chat = driver.find_element(*tid("chat-message-list"))
    before = len(chat.find_elements(By.CSS_SELECTOR, "li"))

    text = f"Guest cheer {uuid.uuid4().hex[:8]}"
    field.send_keys(text)
    driver.find_element(*tid("chat-send-btn")).click()

    wait.until(EC.visibility_of_element_located(tid("progressive-auth-modal")))
    time.sleep(1.0)  # leave room for a late (wrong) append before checking the list
    assert len(chat.find_elements(By.CSS_SELECTOR, "li")) == before
    assert text not in chat.text

    # The block is server-side: the same write from this guest tab is refused with 403.
    status, code = driver.execute_async_script("""
        const done = arguments[arguments.length - 1];
        const tok = sessionStorage.getItem('nascon.guest_token') || 'guest_anon_tok_selenium';
        fetch('/api/v1/chat', {method: 'POST', headers: {'Content-Type': 'application/json',
              'Authorization': 'Bearer ' + tok}, body: JSON.stringify({chat_room_id: 'chat-match-m1', text: 'hi'})})
          .then(r => r.json().then(b => done([r.status, b && b.error ? b.error.code : null]),
                                   () => done([r.status, null])))
          .catch(e => done([0, String(e)]));
    """)
    assert (status, code) == (403, "GUEST_WRITE_FORBIDDEN")


def test_3_guest_cart_and_realtime_score(driver, live_match):
    wait = open_live(driver)
    add = wait.until(EC.element_to_be_clickable(tid("add-to-guest-cart-btn")))
    count = driver.find_element(*tid("guest-cart-count"))
    assert count.text.strip() == "0"
    card = driver.find_element(*tid("pinned-merch-card"))
    assert card.get_attribute("data-variant-id") == "var-jersey-001-L"

    add.click()
    WebDriverWait(driver, 5).until(lambda d: count.text.strip() == "1")

    wait.until(lambda d: body_flag(d, "score-socket") == "open")
    banner = driver.find_element(*tid("live-score-banner"))
    before = banner.text.strip()
    old_home = int(SCORE.match(before).group(1))

    # Repeat the kind of event that scored in this match's own log: no sport or event name in the test.
    status, score = api("GET", "/audit/v1/score/match-m1")
    assert status == 200
    seed = next(e for e in reversed(score["events"]) if {"team_id", "event_type"} <= set(e["payload"]))
    event = {"event_id": f"selenium-{uuid.uuid4().hex}", "match_id": "match-m1", "type": seed["type"],
             "payload": {**seed["payload"], "team_id": score["home"]["team_id"]}}
    status, body = api("POST", "/audit/v1/events", event)
    assert status == 201, body

    WebDriverWait(driver, 0.5, poll_frequency=0.02).until(lambda d: banner.text.strip() != before)
    new_home = int(SCORE.match(banner.text.strip()).group(1))
    assert new_home > old_home


def test_5_login_from_the_modal_merges_the_cart_and_sends_the_chat(driver, live_match):
    """FR-4.4: logging in from the progressive modal keeps the staged cart and completes the blocked write."""
    wait = open_live(driver)
    wait.until(EC.element_to_be_clickable(tid("add-to-guest-cart-btn"))).click()
    count = driver.find_element(*tid("guest-cart-count"))
    WebDriverWait(driver, 5).until(lambda d: count.text.strip() == "1")

    text = f"Member cheer {uuid.uuid4().hex[:8]}"
    driver.find_element(*tid("chat-input-field")).send_keys(text)
    driver.find_element(*tid("chat-send-btn")).click()
    modal = wait.until(EC.visibility_of_element_located(tid("progressive-auth-modal")))
    modal.find_element(By.ID, "auth-email").send_keys(MEMBER_EMAIL)
    modal.find_element(By.ID, "auth-password").send_keys(MEMBER_PASSWORD + Keys.ENTER)

    wait.until(EC.invisibility_of_element_located(tid("progressive-auth-modal")))
    assert driver.execute_script("return sessionStorage.getItem('nascon.member_token');")
    WebDriverWait(driver, 5).until(lambda d: int(count.text.strip() or 0) >= 1)
    chat = driver.find_element(*tid("chat-message-list"))
    WebDriverWait(driver, 5).until(lambda d: text in chat.text)


def test_4_accessibility_dom(driver, live_match):
    """NFR-8: labelled media controls, 48 x 48 px touch targets, Tab/Enter operation and a phone-width layout."""
    wait = open_live(driver)
    wait.until(EC.visibility_of_element_located(tid("guest-video-player")))
    assert driver.find_element(*tid("guest-video-player")).get_attribute("aria-label")
    controls = driver.find_elements(By.CSS_SELECTOR, ".controls button")
    assert len(controls) >= 3 and all(c.get_attribute("aria-label") for c in controls)

    for el in driver.find_elements(By.CSS_SELECTOR, "button, input, a"):
        if el.is_displayed():
            assert el.size["width"] >= 48 and el.size["height"] >= 48, (el.get_attribute("outerHTML")[:80], el.size)

    # Keyboard only: Tab to the add-to-cart button and press Enter.
    driver.find_element(By.TAG_NAME, "body").send_keys(Keys.TAB)
    for _ in range(25):
        if driver.execute_script("return document.activeElement.getAttribute('data-testid');") == "add-to-guest-cart-btn":
            break
        driver.switch_to.active_element.send_keys(Keys.TAB)
    else:
        raise AssertionError("add-to-cart button not reachable with Tab")
    driver.switch_to.active_element.send_keys(Keys.ENTER)
    WebDriverWait(driver, 5).until(lambda d: d.find_element(*tid("guest-cart-count")).text.strip() == "1")

    driver.set_window_size(412, 900)
    WebDriverWait(driver, 5).until(lambda d: d.execute_script("return window.innerWidth;") <= 500)
    overflow = driver.execute_script("return document.documentElement.scrollWidth - window.innerWidth;")
    assert overflow <= 0, f"horizontal overflow of {overflow}px at phone width"
    assert driver.find_element(*tid("live-score-banner")).is_displayed()
