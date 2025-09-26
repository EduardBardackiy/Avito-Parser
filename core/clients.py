from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional
import random

from curl_cffi import requests as curl_requests
from playwright.sync_api import sync_playwright
from anticaptchaofficial.recaptchav2proxyless import recaptchaV2Proxyless

from config.settings import get_settings
from utils.logger import get_logger
from utils.helpers import sanitize_query_params, load_proxies
from services.headers import CUSTOM_HEADERS


class CookieStore:
    def __init__(self, path: Optional[str] = None) -> None:
        settings = get_settings()
        self.path = Path(path or settings.cookie_file)
        self.logger = get_logger("cookies")

    def load(self) -> dict:
        if self.path.exists():
            try:
                with self.path.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                self.logger.exception("Failed to load cookies; starting fresh")
        return {"cookies": [], "cookie_dict": {}}

    def save(self, cookies: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)


class CurlClient:
    def __init__(self, cookie_store: Optional[CookieStore] = None) -> None:
        self.settings = get_settings()
        self.logger = get_logger("curl-client")
        self.cookie_store = cookie_store or CookieStore()
        self.session = curl_requests.Session()

        # Use impersonation for better anti-bot evasion
        self.session.impersonate = "chrome"

        # Proxies
        self._proxies = load_proxies(self.settings.proxies_file)

        # Load stored cookies
        stored = self.cookie_store.load()
        # Prefer dict format if available
        cookie_dict = stored.get("cookie_dict", {})
        if isinstance(cookie_dict, dict) and cookie_dict:
            for name, value in cookie_dict.items():
                try:
                    self.session.cookies.set(name, value)
                except Exception:
                    continue
        else:
            for c in stored.get("cookies", []):
                try:
                    name = c.get("name") if isinstance(c, dict) else None
                    value = c.get("value") if isinstance(c, dict) else None
                    if name is not None and value is not None:
                        self.session.cookies.set(name, value)
                except Exception:
                    continue

    def get(self, url: str, params: Optional[Dict] = None, headers: Optional[Dict] = None) -> str:
        import time

        params = sanitize_query_params(params or {})
        base_headers = {**CUSTOM_HEADERS, **(headers or {})}

        max_attempts = 5
        for attempt in range(1, max_attempts + 1):
            # fresh headers each attempt
            req_headers = dict(base_headers)

            # UA selection
            if self.settings.user_agent:
                req_headers["user-agent"] = self.settings.user_agent
            else:
                ua_path = Path(self.settings.user_agent_list_path)
                if ua_path.exists():
                    ua_list = [l.strip() for l in ua_path.read_text(encoding="utf-8").splitlines() if l.strip()]
                    if ua_list:
                        req_headers["user-agent"] = random.choice(ua_list)

            # proxy selection
            request_kwargs = {
                "params": params,
                "headers": req_headers,
                "timeout": self.settings.request_timeout_seconds,
            }
            proxy = None
            if self.settings.proxy_url:
                proxy = self.settings.proxy_url
            elif self._proxies:
                proxy = random.choice(self._proxies)
            if proxy:
                request_kwargs["proxy"] = proxy

            resp = self.session.get(url, **request_kwargs)

            # persist cookies
            try:
                cookie_dict = self.session.cookies.get_dict()  # type: ignore[attr-defined]
            except Exception:
                cookie_dict = {}
            self.cookie_store.save({"cookie_dict": cookie_dict})

            if resp.status_code in (200, 201):
                return resp.text

            # retry on common anti-bot codes
            if resp.status_code in (403, 429, 503):
                backoff = min(10, 2 ** attempt) + random.uniform(0, 0.5)
                self.logger.warning(f"Attempt {attempt}/{max_attempts} got {resp.status_code}. Retrying in {backoff:.1f}s...")
                time.sleep(backoff)
                continue

            # other errors
            resp.raise_for_status()
            return resp.text

        # fallback to Playwright
        self.logger.warning("curl-cffi blocked after retries; falling back to Playwright")
        pw_client = PlaywrightClient(cookie_store=self.cookie_store)
        return pw_client.get(url)


class PlaywrightClient:
    def __init__(self, cookie_store: Optional[CookieStore] = None) -> None:
        self.settings = get_settings()
        self.cookie_store = cookie_store or CookieStore()
        self.logger = get_logger("pw-client")

    def get(self, url: str) -> str:
        with sync_playwright() as p:
            proxy_arg = None
            proxies = load_proxies(self.settings.proxies_file)
            if self.settings.proxy_url:
                proxy_arg = {"server": self.settings.proxy_url}
            elif proxies:
                proxy_arg = {"server": random.choice(proxies)}

            # Choose User-Agent
            user_agent = None
            if self.settings.user_agent:
                user_agent = self.settings.user_agent
            else:
                ua_path = Path(self.settings.user_agent_list_path)
                if ua_path.exists():
                    ua_list = [l.strip() for l in ua_path.read_text(encoding="utf-8").splitlines() if l.strip()]
                    if ua_list:
                        user_agent = random.choice(ua_list)

            browser = p.chromium.launch(headless=True, proxy=proxy_arg)
            context = browser.new_context(user_agent=user_agent) if user_agent else browser.new_context()

            # Load cookies if present
            stored = self.cookie_store.load()
            if stored.get("cookies"):
                try:
                    context.add_cookies(stored["cookies"])  # type: ignore[arg-type]
                except Exception:
                    self.logger.exception("Failed to add stored cookies to context")

            page = context.new_page()
            # Apply headers (exclude UA, already set on context)
            page.set_extra_http_headers({k: v for k, v in CUSTOM_HEADERS.items() if k.lower() != "user-agent"})

            # Increase timeout via settings and wait for network to be idle
            timeout_ms = self.settings.playwright_timeout_ms
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)

            # Ensure list items are rendered
            try:
                page.wait_for_selector('a[data-marker="item-title"]', timeout=timeout_ms)
            except Exception:
                # Try a softer wait on any catalog container
                page.wait_for_selector('[data-marker="catalog-serp"], .iva-item-content-fRmzq, .iva-item-body-oMJBI', timeout=timeout_ms)

            # Scroll to load lazy content
            try:
                for _ in range(8):
                    page.mouse.wheel(0, 1500)
                    page.wait_for_timeout(400)
            except Exception:
                pass

            # Try automatic reCAPTCHA v2 solving if sitekey present
            try:
                site_key = page.eval_on_selector(
                    'div.g-recaptcha, div[data-sitekey], *[data-sitekey]',
                    'el => el.getAttribute("data-sitekey") || (el.classList.contains("g-recaptcha") ? el.getAttribute("data-sitekey") : null)'
                )
            except Exception:
                site_key = None

            if site_key:
                token = self.solve_recaptcha_v2(site_key, page.url)
                if token:
                    try:
                        # Insert the token into the expected textarea and execute callback
                        page.evaluate(
                            "(tok) => {"
                            "let ta = document.querySelector('textarea#g-recaptcha-response');"
                            "if (!ta) { ta = document.createElement('textarea'); ta.id = 'g-recaptcha-response'; ta.name = 'g-recaptcha-response'; ta.style.display='none'; document.body.appendChild(ta); }"
                            "ta.value = tok;"
                            "}",
                            token,
                        )
                        # Attempt callback trigger if exists
                        page.evaluate(
                            "() => { if (window.grecaptcha && grecaptcha.getResponse) { return grecaptcha.getResponse(); } }"
                        )
                        # Often a form submit is required
                        try:
                            page.click('button[type="submit"]', timeout=2000)
                        except Exception:
                            pass
                        page.wait_for_load_state("networkidle", timeout=timeout_ms)
                    except Exception:
                        self.logger.exception("Failed to inject reCAPTCHA token")

            content = page.content()

            # Save cookies
            try:
                cookies = context.cookies()
                self.cookie_store.save({"cookies": cookies})
            except Exception:
                self.logger.exception("Failed to save cookies from Playwright context")

            # Attempt to persist dynamic state/artifacts for debugging parsing
            try:
                from pathlib import Path as _Path
                from datetime import datetime as _dt
                from config.settings import get_settings as _get_settings
                settings = _get_settings()
                ts_dir = _Path(settings.trash_dir) / _dt.now().strftime("%Y%m%d_%H%M%S")
                (ts_dir / "cards").mkdir(parents=True, exist_ok=True)
                # Save rendered HTML again under this timestamp folder
                (ts_dir / "page_rendered.html").write_text(content, encoding="utf-8")
                # Extract initial state if present
                try:
                    state = page.evaluate("() => (window.__INITIAL_STATE__ || window.__AVITO_STATE__ || null)")
                    if state is not None:
                        import json as _json
                        (ts_dir / "initial_state.json").write_text(_json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
                # Extract catalog container HTML if present
                try:
                    container_html = page.eval_on_selector('[data-marker="catalog-serp"]', 'el => el.outerHTML')
                    if container_html:
                        (ts_dir / "catalog_serp.html").write_text(container_html, encoding="utf-8")
                except Exception:
                    pass
                # Extract individual cards outerHTML via browser context
                try:
                    cards_outer = page.eval_on_selector_all('a[data-marker="item-title"]', 'els => els.map(el => { let n=el; for(let i=0;i<8 && n && n.tagName!="DIV";i++) n=n.parentElement; return (n && n.outerHTML) || el.outerHTML; })')
                    if isinstance(cards_outer, list) and cards_outer:
                        for idx, html_block in enumerate(cards_outer, start=1):
                            (ts_dir / "cards" / f"card-{idx:03d}.html").write_text(html_block, encoding="utf-8")
                except Exception:
                    pass
            except Exception:
                pass

            context.close()
            browser.close()
            return content

    def solve_recaptcha_v2(self, site_key: str, page_url: str) -> str | None:
        if not self.settings.anticaptcha_key:
            return None
        solver = recaptchaV2Proxyless()
        solver.set_key(self.settings.anticaptcha_key)
        solver.set_website_url(page_url)
        solver.set_website_key(site_key)
        token = solver.solve_and_return_solution()
        if token and token != 0 and token != "":
            return token
        return None


