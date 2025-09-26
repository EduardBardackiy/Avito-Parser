from __future__ import annotations

from utils.logger import get_logger
from core.parser import Parser, ListingProcessor
from core.clients import CurlClient, PlaywrightClient, CookieStore
from core.dispatcher import DataDispatcher
from database.db import init_db, get_session
from database.models import Listing


def worker_job() -> None:
    logger = get_logger("worker")
    init_db()
    parser = Parser()
    processor = ListingProcessor()
    cookie_store = CookieStore()
    client = CurlClient(cookie_store)
    dispatcher = DataDispatcher()

    try:
        html = client.get("https://www.avito.ru")
        data = processor.extract(html, url="https://www.avito.ru")
        logger.info(f"Parsed listing title: {data.get('title')}")
        dispatcher.dispatch(html)
        # Save into DB
        from database.models import ParsedListing
        import json as _json
        with get_session() as session:
            obj = session.query(ParsedListing).filter_by(url=data["url"]).one_or_none()
            if obj is None:
                obj = ParsedListing(
                    url=data["url"],
                    title=data.get("title"),
                    price=data.get("price"),
                    bail=data.get("bail"),
                    tax=data.get("tax"),
                    services=data.get("services"),
                    address=data.get("address"),
                    description=data.get("description"),
                    images_json=_json.dumps(data.get("images") or [], ensure_ascii=False),
                )
                session.add(obj)
            else:
                obj.title = data.get("title")
                obj.price = data.get("price")
                obj.bail = data.get("bail")
                obj.tax = data.get("tax")
                obj.services = data.get("services")
                obj.address = data.get("address")
                obj.description = data.get("description")
                obj.images_json = _json.dumps(data.get("images") or [], ensure_ascii=False)

        with get_session() as session:
            session.merge(Listing(url="https://www.avito.ru", title=title, price=None))
    except Exception as exc:
        logger.exception("Worker job failed: %s", exc)


