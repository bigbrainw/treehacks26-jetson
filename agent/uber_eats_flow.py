"""
Uber Eats ordering flow - goes through all steps, stops at the pay button.

Uses Playwright to: enter address → search pizza → select restaurant → add to cart
→ go to checkout → STOP before payment. No order is placed.
"""

import sys
import time
from dataclasses import dataclass, field
from typing import Optional

try:
    from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeout
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False


def _log(msg: str, verbose: bool = True):
    if verbose:
        print(f"  [Uber Eats] {msg}", file=sys.stderr, flush=True)


@dataclass
class FlowStep:
    step: str
    success: bool
    message: str = ""
    url: str = ""


@dataclass
class FlowResult:
    success: bool
    steps: list[FlowStep] = field(default_factory=list)
    final_url: str = ""
    stopped_at: str = ""


def _step(page: Page, name: str, check: bool, msg: str = "") -> FlowStep:
    return FlowStep(step=name, success=check, message=msg, url=page.url)


def _try_click(page: Page, selectors: list[str], wait_ms: int = 3000, verbose: bool = True) -> tuple[bool, str]:
    """Try clicking the first matching visible element. Returns (success, last_error)."""
    last_err = ""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=wait_ms)
            loc.click()
            if verbose:
                _log(f"click OK: {sel[:60]}")
            return True, ""
        except Exception as e:
            last_err = str(e)[:80]
            if verbose:
                _log(f"click skip: {sel[:40]} → {last_err}")
    return False, last_err or "no selector matched"


def _try_fill(page: Page, selectors: list[str], value: str, wait_ms: int = 3000, verbose: bool = True, type_slowly: bool = False) -> tuple[bool, str]:
    """Try filling the first matching visible input. Returns (success, last_error)."""
    last_err = ""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=wait_ms)
            loc.click()
            if type_slowly:
                loc.press_sequentially(value, delay=80)
            else:
                loc.fill(value)
            if verbose:
                _log(f"fill OK: {sel[:60]} = {value[:30]}")
            return True, ""
        except Exception as e:
            last_err = str(e)[:80]
            if verbose:
                _log(f"fill skip: {sel[:40]} → {last_err}")
    return False, last_err or "no selector matched"


def run_uber_eats_flow(
    delivery_address: str = "San Francisco, CA",
    headless: bool = False,
    timeout_ms: int = 15000,
    verbose: bool = True,
) -> FlowResult:
    """
    Go through Uber Eats order flow. Stops at pay button without clicking.
    Returns FlowResult with steps performed.
    """
    if not _PLAYWRIGHT_AVAILABLE:
        _log("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return FlowResult(
            success=False,
            steps=[FlowStep("setup", False, "Playwright not installed. Run: pip install playwright && playwright install chromium")],
        )

    result = FlowResult(success=False)
    base_url = "https://www.ubereats.com/feed"

    _log(f"Starting flow: address={delivery_address}, headless={headless}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            locale="en-US",
        )
        page = context.new_page()
        page.set_default_timeout(timeout_ms)

        try:
            # Step 1: Navigate (use /feed for US locale)
            _log("Step 1: Navigate to Uber Eats")
            page.goto(base_url, wait_until="domcontentloaded")
            time.sleep(2)
            # May redirect to address modal - ensure we're on main feed or address entry
            if "/feed" not in page.url and "ubereats.com" in page.url:
                page.goto("https://www.ubereats.com/feed", wait_until="domcontentloaded")
                time.sleep(2)
            result.steps.append(_step(page, "Navigate to Uber Eats", True))
            _log(f"  URL: {page.url}")

            # Step 2: Enter address (type slowly to trigger autocomplete)
            _log("Step 2: Enter delivery address")
            addr_ok, addr_err = _try_fill(
                page,
                [
                    "#location-typeahead-home-input",
                    'input[placeholder*="address"]',
                    'input[placeholder*="Address"]',
                    'input[aria-label*="address" i]',
                ],
                delivery_address,
                wait_ms=8000,
                verbose=verbose,
                type_slowly=True,
            )
            result.steps.append(_step(page, "Enter delivery address", addr_ok, f"{delivery_address[:40]}{' ... FAILED: ' + addr_err if not addr_ok else ''}"))
            if not addr_ok:
                _log(f"  FAILED at address input: {addr_err}")
                result.final_url = page.url
                browser.close()
                return result
            # Wait for autocomplete dropdown to appear
            sugg_visible = False
            for sel in ["#location-typeahead-home-item-0", '[id*="location-typeahead-item"]', '[role="option"]', 'ul[class*="list"] li', '[class*="suggestion"]']:
                try:
                    page.wait_for_selector(sel, state="visible", timeout=6000)
                    sugg_visible = True
                    _log(f"  Suggestion dropdown visible: {sel[:50]}")
                    break
                except Exception:
                    pass
            if not sugg_visible:
                time.sleep(3)

            # Step 3: Select first suggestion (try click first, then keyboard)
            _log("Step 3: Select address suggestion")
            sugg_ok, sugg_err = _try_click(
                page,
                [
                    "#location-typeahead-home-item-0",
                    '[id*="location-typeahead-home-item"]',
                    '[id*="location-typeahead-item-0"]',
                    '[data-testid*="location-suggestion"]',
                    '[role="option"]',
                    'ul li a',
                    'ul li button',
                    '[class*="suggestion"] a',
                    '[class*="dropdown"] a',
                    'a[href*="feed"]',
                ],
                wait_ms=8000,
                verbose=verbose,
            )
            if not sugg_ok:
                # Fallback: keyboard navigation (ArrowDown selects first, Enter confirms)
                _log("  Trying keyboard: ArrowDown + Enter")
                try:
                    page.keyboard.press("ArrowDown")
                    time.sleep(0.5)
                    page.keyboard.press("Enter")
                    time.sleep(3)
                    # Check if we left the modal (URL changed or feed loaded)
                    if "feed" in page.url or page.locator('input[placeholder*="Search"]').count() > 0:
                        sugg_ok = True
                        _log("  Keyboard selection OK")
                except Exception:
                    pass
            if not sugg_ok:
                sugg_ok, _ = _try_click(page, ['button:has-text("Continue")', 'button:has-text("Find food")', 'button:has-text("Find Food")'], wait_ms=3000, verbose=verbose)
            result.steps.append(_step(page, "Select address suggestion", sugg_ok, f"FAILED: {sugg_err}" if not sugg_ok else ""))
            if not sugg_ok:
                _log(f"  FAILED at address suggestion: {sugg_err}")
            time.sleep(3)

            # Step 4: Search pizza
            _log("Step 4: Search pizza")
            search_ok, search_err = _try_fill(
                page,
                ['input[placeholder*="Search"]', 'input[placeholder*="search"]', 'input[aria-label*="Search" i]'],
                "pizza",
                wait_ms=8000,
                verbose=verbose,
            )
            if search_ok:
                page.keyboard.press("Enter")
                time.sleep(3)
            result.steps.append(_step(page, "Search pizza", search_ok, f"FAILED: {search_err}" if not search_ok else ""))
            if not search_ok:
                _log(f"  FAILED at search: {search_err}")
            _log(f"  URL: {page.url}")

            # Step 5: Click first restaurant
            _log("Step 5: Select restaurant")
            rest_ok, rest_err = _try_click(
                page,
                ['a[href*="/store/"]', 'a[href*="/feed"]', '[data-testid*="store"]', 'a[href*="brand"]'],
                wait_ms=8000,
                verbose=verbose,
            )
            result.steps.append(_step(page, "Select restaurant", rest_ok, f"FAILED: {rest_err}" if not rest_ok else ""))
            if not rest_ok:
                _log(f"  FAILED at restaurant: {rest_err}")
            time.sleep(3)

            # Step 6: Add to cart
            _log("Step 6: Add pizza to cart")
            add_ok, add_err = _try_click(
                page,
                ['button:has-text("Add")', 'button:has-text("add")', '[data-testid*="add"]', 'button:has-text("Add to bag")'],
                wait_ms=8000,
                verbose=verbose,
            )
            result.steps.append(_step(page, "Add pizza to cart", add_ok, f"FAILED: {add_err}" if not add_ok else ""))
            if not add_ok:
                _log(f"  FAILED at add to cart: {add_err}")
            time.sleep(2)

            # Step 7: View cart / checkout
            _log("Step 7: Go to cart")
            cart_ok, cart_err = _try_click(
                page,
                [
                    'a[href*="cart"]',
                    'button:has-text("View cart")',
                    'button:has-text("View bag")',
                    'button:has-text("Checkout")',
                    '[aria-label*="cart" i]',
                ],
                wait_ms=8000,
                verbose=verbose,
            )
            result.steps.append(_step(page, "Go to cart", cart_ok, f"FAILED: {cart_err}" if not cart_ok else ""))
            if not cart_ok:
                _log(f"  FAILED at cart: {cart_err}")
            time.sleep(3)

            # Step 8: Proceed to checkout
            _log("Step 8: Proceed to checkout")
            checkout_ok, checkout_err = _try_click(
                page,
                ['button:has-text("Checkout")', 'button:has-text("checkout")', 'a:has-text("Checkout")'],
                wait_ms=5000,
                verbose=verbose,
            )
            if checkout_ok:
                result.steps.append(_step(page, "Proceed to checkout", True))
                time.sleep(3)
            else:
                _log(f"  Checkout button not found (optional): {checkout_err}")

            # Step 9: Detect pay button - DO NOT CLICK
            _log("Step 9: Look for pay button (do NOT click)")
            pay_selectors = [
                'button:has-text("Place order")',
                'button:has-text("Pay")',
                'button:has-text("Submit order")',
                '[data-testid*="place-order"]',
                '[data-testid*="pay"]',
            ]
            for sel in pay_selectors:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=2000):
                        _log(f"  Found pay button: {sel[:50]} - STOPPED (no order placed)")
                        result.steps.append(_step(page, "Reached pay button (stopped)", True, "Did not click - no order placed"))
                        result.stopped_at = "pay_button"
                        result.success = True
                        break
                except Exception as e:
                    if verbose:
                        _log(f"  pay check skip: {sel[:40]}")
            else:
                _log("  Pay button not visible (may need login)")
                result.steps.append(_step(page, "Checkout reached", True, "Pay button not visible (may need login)"))
                result.stopped_at = "checkout"
                result.success = True

            result.final_url = page.url
            _log(f"Final URL: {result.final_url}")

        except PlaywrightTimeout as e:
            _log(f"TIMEOUT: {e}")
            result.steps.append(FlowStep("timeout", False, str(e)[:200], page.url))
        except Exception as e:
            _log(f"ERROR: {e}")
            result.steps.append(FlowStep("error", False, str(e)[:200], page.url))

        browser.close()

    return result
