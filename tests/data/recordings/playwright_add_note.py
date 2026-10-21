import re
from playwright.sync_api import Playwright, sync_playwright, expect


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("http://127.0.0.1:8911/login")
    page.get_by_placeholder("Username").click()
    page.get_by_placeholder("Username").fill("demo")
    page.get_by_placeholder("Password").fill("demo123")
    page.get_by_role("button", name="Log in").click()
    expect(page).to_have_url(re.compile(".*dashboard"))
    page.locator("#note-input").fill("recorded note py")
    page.locator("#add-note-btn").click()
    expect(page.locator("#notes")).to_contain_text("recorded note py")

    # ---------------------
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
