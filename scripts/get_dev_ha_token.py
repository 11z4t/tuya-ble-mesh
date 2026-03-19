#!/usr/bin/env python3
"""Create a long-lived HA token via Playwright UI automation.
Saves token to /tmp/dev_ha_token (not displayed).
"""
import asyncio
from playwright.async_api import async_playwright

HA_URL = "http://192.168.9.10:8123"
USERNAME = "test"
PW_FILE = "/tmp/dev_ha_pw"  # Read from file, never inline


async def main() -> None:
    try:
        with open(PW_FILE) as f:
            pw = f.read().strip()
    except FileNotFoundError:
        print(f"ERROR: Write password to {PW_FILE} first")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # Login
        await page.goto(f"{HA_URL}/auth/authorize?response_type=code&client_id={HA_URL}/&redirect_uri={HA_URL}/")
        await page.goto(HA_URL)
        await page.wait_for_load_state("networkidle")

        # Fill login form
        try:
            await page.fill("input[name='username']", USERNAME, timeout=10000)
            await page.fill("input[name='password']", pw, timeout=5000)
            await page.click("button[type='submit']", timeout=5000)
            await page.wait_for_load_state("networkidle", timeout=15000)
            print("Logged in")
        except Exception as e:
            print(f"Login error: {e}")
            await browser.close()
            return

        # Navigate to profile security page
        await page.goto(f"{HA_URL}/profile/security")
        await page.wait_for_load_state("networkidle", timeout=10000)

        # Create long-lived token
        try:
            # Click "Create token" button
            await page.click("text=Create Token", timeout=5000)
            await page.wait_for_selector("input[placeholder*='name' i], input[placeholder*='Token' i]", timeout=5000)
            await page.fill("input", "thor-vm903-autotest")
            await page.click("mwc-button[label='OK'], button:has-text('OK')", timeout=5000)
            await page.wait_for_timeout(2000)

            # Get token from clipboard or visible text
            token_el = await page.query_selector("ha-alert code, code, pre")
            if token_el:
                token = await token_el.inner_text()
                token = token.strip()
                with open("/tmp/dev_ha_token", "w") as f:
                    f.write(token)
                print(f"Token saved ({len(token)} chars)")
            else:
                print("Could not find token in UI")
        except Exception as e:
            print(f"Token creation error: {e}")
            # Take screenshot for debugging
            await page.screenshot(path="/tmp/ha_profile_screenshot.png")
            print("Screenshot saved to /tmp/ha_profile_screenshot.png")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
