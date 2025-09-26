from __future__ import annotations

from typing import Optional

import random
from pathlib import Path
from bs4 import BeautifulSoup
import json
import re

from utils.logger import get_logger
from utils.helpers import sanitize_query_params
from config.settings import get_settings


class Parser:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.logger = get_logger("parser")
        # Load UA list if provided and no explicit UA
        if not self.settings.user_agent:
            ua_path = Path(self.settings.user_agent_list_path)
            if ua_path.exists():
                self._user_agents = [line.strip() for line in ua_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            else:
                self._user_agents = []
        else:
            self._user_agents = []

    def parse_title(self, html: str) -> str:
        soup = BeautifulSoup(html, "lxml")
        title_tag = soup.find("title")
        return title_tag.text.strip() if title_tag else ""

    def pick_user_agent(self) -> str | None:
        if self.settings.user_agent:
            return self.settings.user_agent
        if getattr(self, "_user_agents", None):
            return random.choice(self._user_agents)
        return None


class ListingProcessor:
    def __init__(self) -> None:
        self.logger = get_logger("processor")

    def extract(self, html: str, url: str) -> dict:
        soup = BeautifulSoup(html, "lxml")

        def text_or_none(node):
            return node.get_text(strip=True) if node else None

        title = text_or_none(soup.select_one('h1, h1[itemprop="name"]'))
        # Price patterns
        price = None
        price_node = soup.find(string=lambda s: isinstance(s, str) and "₽" in s)
        if price_node:
            price = price_node.strip()

        # Bail, tax, services - heuristic by keywords
        def find_by_keyword(keyword: str) -> str | None:
            el = soup.find(string=lambda s: isinstance(s, str) and keyword in s)
            return el.strip() if el else None

        bail = find_by_keyword("Залог")
        tax = find_by_keyword("Комиссия")
        services = find_by_keyword("ЖКУ")

        # Address
        address = None
        addr_node = soup.select_one('[data-marker="delivery-location"]') or soup.find("address")
        address = text_or_none(addr_node)

        # Description
        desc = None
        desc_node = soup.select_one('[data-marker="item-description/text"]') or soup.select_one('div[itemprop="description"]')
        desc = text_or_none(desc_node)

        # Images from carousel
        images = []
        for img in soup.select('img[src*="/image"]'):
            src = img.get("src")
            if src and src not in images:
                images.append(src)

        return {
            "url": url,
            "title": title,
            "price": price,
            "bail": bail,
            "tax": tax,
            "services": services,
            "address": address,
            "description": desc,
            "images": images,
        }


class ListingListProcessor:
    def __init__(self) -> None:
        self.logger = get_logger("list-processor")
        self._space_re = re.compile(r"[\s\u00A0]+")

    def _normalize(self, text: str | None) -> str:
        if not text:
            return ""
        return self._space_re.sub(" ", text).strip()

    def _extract_digits(self, text: str | None) -> int | None:
        if not text:
            return None
        try:
            digits = re.sub(r"[^\d]", "", str(text))
            return int(digits) if digits else None
        except Exception:
            return None

    def _extract_percentage(self, text: str | None) -> int | None:
        if not text:
            return None
        try:
            m = re.search(r"(\d+)%", text)
            if m:
                return int(m.group(1))
            m = re.search(r"комиссия\s*(\d+)", text.lower())
            if m:
                return int(m.group(1))
        except Exception:
            return None
        return None

    def extract_all(self, html: str, base_url: str | None = None) -> list[dict]:
        def log_diagnostics(soup: BeautifulSoup) -> None:
            try:
                selector_groups = {
                    "cards.primary": 'div.iva-item-content-fRmzq, div.iva-item-body-oMJBI',
                    "cards.fallback": 'div[data-marker="item"], div.iva-item-root, div.index-root, .js-catalog-item, [data-marker*="item"]',
                    "title.links": 'a[data-marker="item-title"], .iva-item-title a, a[href*="/item/"]',
                    "price.all": 'p[data-marker="item-price"], span[data-marker="item-price"], .iva-item-price, [data-marker*="price"], .price-price',
                    "address.all": '[data-marker="item-address"], .iva-item-address, [class*="address"], [data-marker*="address"]',
                    "desc.all": 'div.iva-item-bottomBlock-VewGa p, [data-marker*="description"], [class*="description"]',
                }
                for name, sel in selector_groups.items():
                    try:
                        cnt = len(soup.select(sel))
                    except Exception:
                        cnt = -1
                    self.logger.info(f"diag:{name} -> {cnt}")
                # Regex-based indicators
                try:
                    href_item = len(re.findall(r'href="[^"]*?/item/[^"#\\?]+', soup.decode() if hasattr(soup, 'decode') else str(soup)))
                except Exception:
                    href_item = -1
                self.logger.info(f"diag:regex.href_/item/ -> {href_item}")
            except Exception:
                pass

        def extract_from_soup(soup: BeautifulSoup) -> list[dict]:
            log_diagnostics(soup)
            cards_local: list[dict] = []

            # Avito card roots commonly contain these classes/markers
            card_nodes = soup.select('div.iva-item-content-fRmzq, div.iva-item-body-oMJBI')
            if not card_nodes:
                # fallback to broader markers
                card_nodes = soup.select(
                    'div[data-marker="item"], div.iva-item-root, div.index-root, .js-catalog-item, [data-marker*="item"]'
                )

            for card in card_nodes:
                item: dict = {}

                # Title and link
                a = (
                    card.select_one('a[data-marker="item-title"][href]')
                    or card.select_one('.iva-item-title a[href]')
                    or card.select_one('a[href*="/item/"]')
                    or card.select_one('a[title][href]')
                )
                if a:
                    item["title"] = self._normalize(a.get("title") or a.get_text(" "))
                    href = a.get("href")
                    if href and href.startswith("/") and base_url:
                        item["url"] = base_url.rstrip("/") + href
                    else:
                        item["url"] = href

                # Price
                price_el = (
                    card.select_one('p[data-marker="item-price"]')
                    or card.select_one('span[data-marker="item-price"]')
                    or card.select_one('.iva-item-price')
                    or card.select_one('[data-marker*="price"]')
                    or card.select_one('.price-price')
                    or card.select_one('[itemprop="offers"]')
                )
                if price_el:
                    price_raw = self._normalize(price_el.get_text(" "))
                    item["price"] = price_raw
                    item["price_raw"] = price_raw
                    item["price_value"] = self._extract_digits(price_raw)

                # Params line: Залог · Комиссия · ЖКУ ...
                params_p = card.select_one('p[data-marker="item-specific-params"]')
                bail = tax = services = None
                bail_raw = commission_raw = services_raw = None
                bail_value = commission_value = None
                if params_p:
                    params_text = self._normalize(params_p.get_text(" "))
                    parts = [p.strip() for p in params_text.split("·")]
                    for part in parts:
                        if part.startswith("Залог"):
                            bail = part
                            bail_raw = part
                            bail_value = self._extract_digits(part)
                        elif part.startswith("Комиссия") or part.startswith("Без комиссии"):
                            tax = part
                            commission_raw = part
                            commission_value = self._extract_percentage(part)
                        elif part.startswith("ЖКУ") or "счетчики" in part:
                            services = part
                            services_raw = part
                item["bail"] = bail
                item["tax"] = tax
                item["services"] = services
                if bail_raw:
                    item["bail_raw"] = bail_raw
                if bail_value is not None:
                    item["bail_value"] = bail_value
                if commission_raw:
                    item["commission_raw"] = commission_raw
                if commission_value is not None:
                    item["commission"] = commission_value
                if services_raw:
                    item["services_raw"] = services_raw

                # Address
                addr_blk = (
                    card.select_one('[data-marker="item-address"]')
                    or card.select_one('.iva-item-address')
                    or card.select_one('[class*="address"]')
                    or card.select_one('[data-marker*="address"]')
                )
                if addr_blk:
                    addr_main = self._normalize(addr_blk.get_text(" "))
                    # optional next line with district/metro/mins
                    extra = None
                    next_p = addr_blk.find_next('p')
                    if next_p and addr_blk != next_p:
                        extra = self._normalize(next_p.get_text(" "))
                    item["address"] = f"{addr_main}{(', ' + extra) if extra else ''}"

                # Description (short)
                desc_p = (
                    card.select_one('div.iva-item-bottomBlock-VewGa p')
                    or card.select_one('[data-marker="item-description"]')
                    or card.select_one('[data-marker*="description"]')
                    or card.select_one('[class*="description"]')
                )
                if desc_p:
                    item["description"] = self._normalize(desc_p.get_text(" "))

                # Images from carousel within the card
                imgs: list[str] = []
                for img in card.select('img.photo-slider-image-cD891[src], img[data-marker="image"], .iva-item-image img, [data-marker*="image"] img'):
                    src = img.get("src")
                    if src and src not in imgs:
                        if src.startswith('//'):
                            src = 'https:' + src
                        elif src.startswith('/') and base_url:
                            src = base_url.rstrip('/') + src
                        imgs.append(src)
                item["images"] = imgs

                if any(item.get(k) for k in ("title", "url")):
                    cards_local.append(item)

            # If no cards assembled but there are title links on page level, build items from links
            if not cards_local:
                link_nodes = soup.select('a[data-marker="item-title"][href], .iva-item-title a[href], a[href*="/item/"]')
                self.logger.info(f"diag:page.links -> {len(link_nodes)}")
                for a in link_nodes:
                    try:
                        title = self._normalize(a.get("title") or a.get_text(" "))
                        href = a.get("href")
                        if not href:
                            continue
                        url = href
                        if url.startswith('/') and base_url:
                            url = base_url.rstrip('/') + url
                        # Try to find a nearby container to enrich price/address
                        container = a
                        for _ in range(6):
                            if container and container.name != 'div':
                                container = container.parent
                        item: dict = {"title": title, "url": url}
                        price_el = (
                            container.select_one('p[data-marker="item-price"]') if container else None
                        ) or a.find_next('p', attrs={"data-marker": "item-price"})
                        if price_el:
                            price_raw = self._normalize(price_el.get_text(" "))
                            item["price"] = price_raw
                            item["price_raw"] = price_raw
                            item["price_value"] = self._extract_digits(price_raw)
                        addr_el = (
                            container.select_one('[data-marker="item-address"]') if container else None
                        ) or a.find_next(attrs={"data-marker": "item-address"})
                        if addr_el:
                            item["address"] = self._normalize(addr_el.get_text(" "))
                        cards_local.append(item)
                    except Exception:
                        continue

            return cards_local

        # First pass with lxml
        soup = BeautifulSoup(html, "lxml")
        cards: list[dict] = extract_from_soup(soup)

        # Avito card roots commonly contain these classes/markers
        card_nodes = soup.select('div.iva-item-content-fRmzq, div.iva-item-body-oMJBI')
        if not card_nodes:
            # fallback to broader markers
            card_nodes = soup.select(
                'div[data-marker="item"], div.iva-item-root, div.index-root, .js-catalog-item, [data-marker*="item"]'
            )

        # Second pass with built-in parser for messy HTML dumps
        if not cards:
            soup2 = BeautifulSoup(html, "html.parser")
            cards = extract_from_soup(soup2)

        # Always try to parse structured data from scripts (ld+json) and merge with existing results
        scripted = self._extract_from_scripts(soup, base_url=base_url)
        
        # Объединяем результаты: сначала HTML парсинг, потом JSON-LD
        all_cards = cards + scripted
        
        return all_cards

    def _extract_from_scripts(self, soup: BeautifulSoup, base_url: str | None = None) -> list[dict]:
        items: list[dict] = []
        for tag in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(tag.string or "{}")
            except Exception:
                continue
            
            # Обрабатываем структуру @graph
            if isinstance(data, dict) and "@graph" in data:
                graph = data["@graph"]
                if isinstance(graph, list):
                    for graph_item in graph:
                        if isinstance(graph_item, dict) and "offers" in graph_item:
                            offers = graph_item["offers"]
                            if isinstance(offers, dict) and "offers" in offers:
                                offers_list = offers["offers"]
                                if isinstance(offers_list, list):
                                    for offer in offers_list:
                                        if isinstance(offer, dict):
                                            # Извлекаем данные из предложения
                                            name = offer.get("name")
                                            url = offer.get("url")
                                            price = offer.get("price")
                                            currency = offer.get("priceCurrency")
                                            image = offer.get("image")
                                            
                                            # Формируем цену
                                            price_str = None
                                            if price:
                                                price_str = f"{price} {currency}" if currency else str(price)
                                            
                                            # Формируем изображения
                                            images = []
                                            if image:
                                                if isinstance(image, list):
                                                    images = [i for i in image if isinstance(i, str)]
                                                elif isinstance(image, str):
                                                    images = [image]
                                            
                                            # Обрабатываем URL
                                            if url and url.startswith("/") and base_url:
                                                url = base_url.rstrip("/") + url
                                            
                                            candidate = {
                                                "title": name,
                                                "price": price_str,
                                                "url": url,
                                                "images": images,
                                            }
                                            if any(candidate.get(k) for k in ("title", "url")):
                                                items.append(candidate)
            
            # Обрабатываем старую структуру для совместимости
            payloads = data if isinstance(data, list) else [data]
            for obj in payloads:
                if not isinstance(obj, dict):
                    continue
                # Accept Listing / Product-like entries
                name = obj.get("name") or obj.get("headline")
                offers = obj.get("offers")
                price = None
                if isinstance(offers, dict):
                    p = offers.get("price")
                    cur = offers.get("priceCurrency")
                    if p:
                        price = f"{p} {cur}" if cur else str(p)
                images = []
                img = obj.get("image")
                if isinstance(img, list):
                    images = [i for i in img if isinstance(i, str)]
                elif isinstance(img, str):
                    images = [img]
                url = obj.get("url")
                if url and url.startswith("/") and base_url:
                    url = base_url.rstrip("/") + url
                address = None
                addr = obj.get("address")
                if isinstance(addr, dict):
                    address = ", ".join(filter(None, [addr.get("streetAddress"), addr.get("addressLocality")])) or None
                description = obj.get("description")
                candidate = {
                    "title": name,
                    "price": price,
                    "url": url,
                    "address": address,
                    "description": description,
                    "images": images,
                }
                if any(candidate.get(k) for k in ("title", "url")):
                    items.append(candidate)

        # Heuristic for inline initial state JSON
        if not items:
            try:
                scripts = soup.find_all("script")
                for s in scripts:
                    txt = (s.string or "")
                    if "__INITIAL_STATE__" in txt:
                        # naive brace extraction
                        start = txt.find("{")
                        end = txt.rfind("}")
                        if start != -1 and end != -1 and end > start:
                            raw = txt[start : end + 1]
                            try:
                                state = json.loads(raw)
                            except Exception:
                                continue
                            # Attempt to locate items array heuristically
                            def walk(node):
                                if isinstance(node, dict):
                                    for k, v in node.items():
                                        if k.lower() in ("items", "list", "docs") and isinstance(v, list):
                                            for it in v:
                                                yield it
                                        else:
                                            yield from walk(v)
                                elif isinstance(node, list):
                                    for it in node:
                                        yield from walk(it)
                            for it in walk(state):
                                if not isinstance(it, dict):
                                    continue
                                title = it.get("title") or it.get("name")
                                url = it.get("url") or it.get("uri")
                                if url and isinstance(url, str) and url.startswith("/") and base_url:
                                    url = base_url.rstrip("/") + url
                                price = None
                                if isinstance(it.get("price"), dict):
                                    pr = it["price"].get("value") or it["price"].get("price")
                                    cur = it["price"].get("currency")
                                    if pr:
                                        price = f"{pr} {cur}" if cur else str(pr)
                                images = []
                                pics = it.get("images") or it.get("thumbnails")
                                if isinstance(pics, list):
                                    images = [p.get("url") if isinstance(p, dict) else p for p in pics if p]
                                candidate = {
                                    "title": title,
                                    "url": url,
                                    "price": price,
                                    "images": [im for im in images if isinstance(im, str)],
                                }
                                if any(candidate.get(k) for k in ("title", "url")):
                                    items.append(candidate)
                        if items:
                            break
            except Exception:
                pass

        return items

    def _fallback_regex_sweep(self, html: str, base_url: str | None = None) -> list[dict]:
        items: list[dict] = []
        try:
            # Find anchors with /item/ links
            for m in re.finditer(r'<a[^>]+href="([^"]*?/item/[^"#\?]+)[^"]*"[^>]*>(.*?)</a>', html, re.IGNORECASE | re.DOTALL):
                href = m.group(1)
                inner = m.group(2)
                # Strip tags from inner
                title = re.sub(r'<[^>]+>', ' ', inner)
                title = self._normalize(title)
                if not title or len(title) < 3 or title.lower() == 'подробнее':
                    continue
                url = href
                if url.startswith('/') and base_url:
                    url = base_url.rstrip('/') + url
                # Look ahead for a price with ₽ within 300 chars after the link end
                end = m.end()
                tail = html[end:end+300]
                price_match = re.search(r'([\d\s\u00A0]+)\s*₽', tail)
                price_raw = None
                price_value = None
                if price_match:
                    price_raw = self._normalize(price_match.group(0))
                    price_value = self._extract_digits(price_raw)
                candidate = {
                    'title': title,
                    'url': url,
                    'price': price_raw,
                    'price_raw': price_raw,
                    'price_value': price_value,
                }
                items.append(candidate)
            # Deduplicate by URL while preserving order
            seen: set[str] = set()
            deduped: list[dict] = []
            for it in items:
                u = it.get('url')
                if isinstance(u, str) and u not in seen:
                    seen.add(u)
                    deduped.append(it)
            return deduped
        except Exception:
            return []


