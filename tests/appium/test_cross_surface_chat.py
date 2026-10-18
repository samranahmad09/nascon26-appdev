"""Section 6.3 cross-surface scenario: the native app joins co-stream cs-session-101 while a
Selenium Grid browser opens /web/costream/cs-session-101; one chat message posted by a member
must reach both within 3.0 seconds."""
import time
import uuid

import pytest
import requests

from conftest import API_URL, BASE_URL, GRID_URL, fire_deep_link, wait_for_id, wait_for_text

ROOM = "chat-cs-session-101"


@pytest.fixture(scope="module")
def browser():
    from selenium import webdriver

    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    drv = webdriver.Remote(command_executor=GRID_URL, options=opts)
    yield drv
    drv.quit()


@pytest.fixture(scope="module")
def member_token():
    # player-alpha-1 is a co-stream participant, so it may post in the room.
    r = requests.post(f"{API_URL}/api/v1/auth/login",
                      json={"email": "player@nascon.test", "password": "Nascon@26"}, timeout=10)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_chat_reaches_app_and_web_within_3s(driver, browser, costream_session, member_token):
    from selenium.webdriver.common.by import By

    # Native app joins the co-stream through its deep link.
    fire_deep_link("nascon://stream/cs-session-101")
    assert wait_for_id(driver, "screen-costream", timeout=5.0) is not None
    assert wait_for_id(driver, "chat-message-list", timeout=5.0) is not None

    # Guest web player opens the same co-stream.
    browser.get(f"{BASE_URL}/web/costream/cs-session-101")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if browser.find_elements(By.CSS_SELECTOR, '[data-testid="chat-message-list"]'):
            break
        time.sleep(0.1)
    assert browser.find_elements(By.CSS_SELECTOR, '[data-testid="chat-message-list"]')
    time.sleep(1.0)  # let both clients finish subscribing to chat:{room}

    text = f"cross-surface {uuid.uuid4().hex[:8]}"
    posted = time.monotonic()
    r = requests.post(f"{API_URL}/api/v1/chat", json={"chat_room_id": ROOM, "text": text},
                      headers={"Authorization": f"Bearer {member_token}"}, timeout=5)
    assert r.status_code in (200, 201), r.text
    limit = posted + 3.0

    assert wait_for_text(driver, text, timeout=max(0.0, limit - time.monotonic())), \
        "message did not reach the native app within 3.0 s"
    app_latency = time.monotonic() - posted

    web_ok = False
    while time.monotonic() < limit:
        lists = browser.find_elements(By.CSS_SELECTOR, '[data-testid="chat-message-list"]')
        if lists and text in lists[0].text:
            web_ok = True
            break
        time.sleep(0.1)
    assert web_ok, "message did not reach the web player within 3.0 s"
    assert app_latency <= 3.0
