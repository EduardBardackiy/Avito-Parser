from __future__ import annotations

import argparse

import sys
from pathlib import Path

# Ensure project root is on sys.path when running as a script
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.scheduler import run_scheduler
from services.worker import worker_job
from core.clients import CurlClient, PlaywrightClient, CookieStore
from core.dispatcher import DataDispatcher, load_sink
from utils.logger import get_logger
from config.settings import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Avito Parser CLI")
    parser.add_argument("command", nargs="?", default="run-once", choices=["run-once", "schedule", "parse-file"], help="How to run the parser")
    parser.add_argument("--client", choices=["curl", "playwright"], default="curl", help="HTTP client")
    parser.add_argument("--minutes", type=int, default=5, help="Schedule interval in minutes")
    parser.add_argument("--sink", type=str, help="External sink callable 'pkg.module:function'")
    parser.add_argument("--proxy", type=str, help="Proxy URL (overrides settings)")
    parser.add_argument("--proxies-file", type=str, help="Path to proxies file (overrides settings)")
    parser.add_argument("--file", type=str, help="Path to local HTML file for parse-file mode")
    args = parser.parse_args()

    settings = get_settings()
    target_url = settings.target_url
    if not target_url:
        parser.error("TARGET_URL is not set in .env")

    if args.command == "run-once":
        cookie_store = CookieStore()
        client = CurlClient(cookie_store) if args.client == "curl" else PlaywrightClient(cookie_store)
        sink_fn = load_sink(args.sink) if args.sink else None
        dispatcher = DataDispatcher(sink=sink_fn)

        logger = get_logger("cli")
        try:
            # Override proxies if provided for this run
            if args.proxy:
                import os
                os.environ["PROXY_URL"] = args.proxy
            if args.proxies_file:
                import os
                os.environ["PROXIES_FILE"] = args.proxies_file

            html = client.get(target_url)
            dispatcher.dispatch(html)
            # Process multiple cards and save to DB
            from core.parser import ListingListProcessor
            from database.db import init_db, get_session
            from database.models import ParsedListing
            import json as _json

            init_db()
            items = ListingListProcessor().extract_all(html, base_url="https://www.avito.ru")
            logger.info(f"Found {len(items)} item(s) on the page")
            saved = 0
            with get_session() as session:
                seen_urls = {}  # Изменяем на словарь для хранения лучших версий
                for data in items:
                    if not data.get("url"):
                        logger.debug(f"Skip item without url: {data.get('title')}")
                        continue
                    
                    url = data["url"]
                    # Если URL уже встречался, сравниваем качество данных
                    if url in seen_urls:
                        existing = seen_urls[url]
                        # Приоритет: объявления с изображениями лучше объявлений без изображений
                        existing_has_images = bool(existing.get("images"))
                        current_has_images = bool(data.get("images"))
                        
                        if current_has_images and not existing_has_images:
                            logger.debug(f"Replace item without images with item with images: {url}")
                            seen_urls[url] = data
                        elif not current_has_images and existing_has_images:
                            logger.debug(f"Skip item without images, keep item with images: {url}")
                        else:
                            logger.debug(f"Skip duplicate in batch: {url}")
                        continue
                    
                    seen_urls[url] = data
                
                # Теперь обрабатываем уникальные объявления
                for data in seen_urls.values():
                    logger.debug(f"Upsert item: {data.get('title')} | {data.get('url')}")
                    obj = session.query(ParsedListing).filter_by(url=data["url"]).one_or_none()
                    if obj is None:
                        obj = ParsedListing(
                            url=data["url"],
                            title=data.get("title"),
                            price=data.get("price"),
                            price_raw=data.get("price_raw"),
                            price_value=data.get("price_value"),
                            bail=data.get("bail"),
                            bail_raw=data.get("bail_raw"),
                            bail_value=data.get("bail_value"),
                            tax=data.get("tax"),
                            commission_raw=data.get("commission_raw"),
                            commission=data.get("commission"),
                            services=data.get("services"),
                            services_raw=data.get("services_raw"),
                            address=data.get("address"),
                            description=data.get("description"),
                            images_json=_json.dumps(data.get("images") or [], ensure_ascii=False),
                        )
                        session.add(obj)
                        try:
                            session.flush()
                        except Exception:
                            session.rollback()
                            raise
                        saved += 1
                    else:
                        obj.title = data.get("title")
                        obj.price = data.get("price")
                        obj.price_raw = data.get("price_raw")
                        obj.price_value = data.get("price_value")
                        obj.bail = data.get("bail")
                        obj.bail_raw = data.get("bail_raw")
                        obj.bail_value = data.get("bail_value")
                        obj.tax = data.get("tax")
                        obj.commission_raw = data.get("commission_raw")
                        obj.commission = data.get("commission")
                        obj.services = data.get("services")
                        obj.services_raw = data.get("services_raw")
                        obj.address = data.get("address")
                        obj.description = data.get("description")
                        obj.images_json = _json.dumps(data.get("images") or [], ensure_ascii=False)
                        saved += 1
            logger.info(f"Saved/updated {saved} items from list page")
        except Exception as exc:
            logger.exception("Fetch failed: %s", exc)
    elif args.command == "parse-file":
        logger = get_logger("cli")
        try:
            html_path: Path | None = None
            if args.file:
                html_path = Path(args.file)
                if not html_path.exists():
                    parser.error(f"File not found: {args.file}")
            else:
                # Auto-detect the newest Trash/*/page.html or Trash/page.html
                trash_dir = PROJECT_ROOT / "Trash"
                newest_path: Path | None = None
                newest_mtime: float | None = None
                if trash_dir.exists():
                    # Look into subdirectories
                    for child in trash_dir.iterdir():
                        candidate = child / "page.html" if child.is_dir() else None
                        if candidate and candidate.exists():
                            mtime = candidate.stat().st_mtime
                            if newest_mtime is None or mtime > newest_mtime:
                                newest_mtime = mtime
                                newest_path = candidate
                    # Fallback to Trash/page.html at root
                    root_candidate = trash_dir / "page.html"
                    if newest_path is None and root_candidate.exists():
                        newest_path = root_candidate
                if newest_path is None:
                    parser.error("No HTML file provided and nothing found in Trash/*/page.html")
                html_path = newest_path
                logger.info(f"Auto-selected HTML: {html_path}")

            html = html_path.read_text(encoding="utf-8", errors="ignore")

            # Process multiple cards and save to DB
            from core.parser import ListingListProcessor
            from database.db import init_db, get_session
            from database.models import ParsedListing
            import json as _json

            init_db()
            processor = ListingListProcessor()
            items = processor.extract_all(html, base_url="https://www.avito.ru")
            logger.info(f"Found {len(items)} item(s) in file: {html_path}")

            # Fallback: try page_pretty.html if nothing found
            if not items:
                pretty_path = html_path.parent / "page_pretty.html"
                if pretty_path.exists():
                    logger.info(f"Trying fallback file: {pretty_path}")
                    html_pretty = pretty_path.read_text(encoding="utf-8", errors="ignore")
                    items = processor.extract_all(html_pretty, base_url="https://www.avito.ru")
                    logger.info(f"Fallback found {len(items)} item(s) in file: {pretty_path}")

            # Fallback: try scripts.json heuristics if present (dump of page scripts)
            if not items:
                scripts_json = html_path.parent / "scripts.json"
                if scripts_json.exists():
                    try:
                        import json as _json
                        data = _json.loads(scripts_json.read_text(encoding="utf-8", errors="ignore") or "{}")
                        # try to recover __INITIAL_STATE__-like nodes
                        def walk(node):
                            if isinstance(node, dict):
                                for k, v in node.items():
                                    if isinstance(k, str) and k.lower() in ("items", "list", "docs") and isinstance(v, list):
                                        for it in v:
                                            yield it
                                    else:
                                        yield from walk(v)
                            elif isinstance(node, list):
                                for it in node:
                                    yield from walk(it)
                        recovered = []
                        for it in walk(data):
                            if not isinstance(it, dict):
                                continue
                            title = it.get("title") or it.get("name")
                            url = it.get("url") or it.get("uri")
                            if isinstance(url, str) and url.startswith("/"):
                                url = "https://www.avito.ru".rstrip("/") + url
                            price = None
                            if isinstance(it.get("price"), dict):
                                pv = it["price"].get("value") or it["price"].get("price")
                                cur = it["price"].get("currency")
                                if pv:
                                    price = f"{pv} {cur}" if cur else str(pv)
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
                                recovered.append(candidate)
                        if recovered:
                            items = recovered
                            logger.info(f"Recovered {len(items)} item(s) from scripts.json")
                    except Exception as _exc:
                        logger.debug(f"scripts.json recovery failed: {_exc}")

            # Fallback: try page.txt plain dump
            if not items:
                txt_path = html_path.parent / "page.txt"
                if txt_path.exists():
                    logger.info(f"Trying fallback file: {txt_path}")
                    txt_content = txt_path.read_text(encoding="utf-8", errors="ignore")
                    items = processor.extract_all(txt_content, base_url="https://www.avito.ru")
                    logger.info(f"Fallback found {len(items)} item(s) in file: {txt_path}")

            # Final fallback: regex sweep over raw HTML
            if not items:
                from core.parser import ListingListProcessor as _LLP
                try:
                    regex_items = _LLP()._fallback_regex_sweep(html, base_url="https://www.avito.ru")  # type: ignore[attr-defined]
                except Exception:
                    regex_items = []
                if regex_items:
                    logger.info(f"Regex sweep recovered {len(regex_items)} item(s) from raw HTML")
                    items = regex_items
            saved = 0
            with get_session() as session:
                seen_urls = {}  # Изменяем на словарь для хранения лучших версий
                for data in items:
                    if not data.get("url"):
                        logger.debug(f"Skip item without url: {data.get('title')}")
                        continue
                    
                    url = data["url"]
                    # Если URL уже встречался, сравниваем качество данных
                    if url in seen_urls:
                        existing = seen_urls[url]
                        # Приоритет: объявления с изображениями лучше объявлений без изображений
                        existing_has_images = bool(existing.get("images"))
                        current_has_images = bool(data.get("images"))
                        
                        if current_has_images and not existing_has_images:
                            logger.debug(f"Replace item without images with item with images: {url}")
                            seen_urls[url] = data
                        elif not current_has_images and existing_has_images:
                            logger.debug(f"Skip item without images, keep item with images: {url}")
                        else:
                            logger.debug(f"Skip duplicate in batch: {url}")
                        continue
                    
                    seen_urls[url] = data
                
                # Теперь обрабатываем уникальные объявления
                for data in seen_urls.values():
                    logger.debug(f"Upsert item: {data.get('title')} | {data.get('url')}")
                    obj = session.query(ParsedListing).filter_by(url=data["url"]).one_or_none()
                    if obj is None:
                        obj = ParsedListing(
                            url=data["url"],
                            title=data.get("title"),
                            price=data.get("price"),
                            price_raw=data.get("price_raw"),
                            price_value=data.get("price_value"),
                            bail=data.get("bail"),
                            bail_raw=data.get("bail_raw"),
                            bail_value=data.get("bail_value"),
                            tax=data.get("tax"),
                            commission_raw=data.get("commission_raw"),
                            commission=data.get("commission"),
                            services=data.get("services"),
                            services_raw=data.get("services_raw"),
                            address=data.get("address"),
                            description=data.get("description"),
                            images_json=_json.dumps(data.get("images") or [], ensure_ascii=False),
                        )
                        session.add(obj)
                        saved += 1
                    else:
                        obj.title = data.get("title")
                        obj.price = data.get("price")
                        obj.price_raw = data.get("price_raw")
                        obj.price_value = data.get("price_value")
                        obj.bail = data.get("bail")
                        obj.bail_raw = data.get("bail_raw")
                        obj.bail_value = data.get("bail_value")
                        obj.tax = data.get("tax")
                        obj.commission_raw = data.get("commission_raw")
                        obj.commission = data.get("commission")
                        obj.services = data.get("services")
                        obj.services_raw = data.get("services_raw")
                        obj.address = data.get("address")
                        obj.description = data.get("description")
                        obj.images_json = _json.dumps(data.get("images") or [], ensure_ascii=False)
                        saved += 1
            logger.info(f"Saved/updated {saved} items from file")
        except Exception as exc:
            logger.exception("parse-file failed: %s", exc)
    elif args.command == "schedule":
        run_scheduler(worker_job, every_minutes=args.minutes)


if __name__ == "__main__":
    main()


