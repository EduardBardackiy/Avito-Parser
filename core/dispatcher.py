from __future__ import annotations

from typing import Callable, Optional
import importlib

from utils.logger import get_logger
from config.settings import get_settings
from pathlib import Path
from datetime import datetime
import json
from bs4 import BeautifulSoup


class DataDispatcher:
    def __init__(self, sink: Optional[Callable[[str], None]] = None) -> None:
        self.sink = sink
        self.logger = get_logger("dispatcher")

    def dispatch(self, text: str) -> None:
        # Always print to console for now
        print(text)
        if self.sink is not None:
            try:
                self.sink(text)
            except Exception as exc:
                self.logger.exception("External sink failed: %s", exc)
        # Save artifacts to Trash for inspection
        try:
            settings = get_settings()
            trash_dir = Path(settings.trash_dir)
            ts_dir = trash_dir / datetime.now().strftime("%Y%m%d_%H%M%S")
            (ts_dir / "cards").mkdir(parents=True, exist_ok=True)

            # Raw HTML
            (ts_dir / "page.html").write_text(text, encoding="utf-8")

            # Prettified HTML
            try:
                soup = BeautifulSoup(text, "lxml")
                (ts_dir / "page_pretty.html").write_text(soup.prettify(), encoding="utf-8")
            except Exception:
                pass

            # Plain text dump
            try:
                plain = soup.get_text("\n", strip=True)
                (ts_dir / "page.txt").write_text(plain, encoding="utf-8")
            except Exception:
                pass

            # Extract JSON scripts (ld+json) and initial state
            scripts: dict = {"ld_json": [], "inline_state": []}
            try:
                for tag in soup.select('script[type="application/ld+json"]'):
                    try:
                        scripts["ld_json"].append(json.loads(tag.string or "{}"))
                    except Exception:
                        scripts["ld_json"].append({"raw": tag.string})
                for tag in soup.select("script"): 
                    txt = (tag.string or "").strip()
                    if "__INITIAL_STATE__" in txt or "window.__" in txt:
                        scripts["inline_state"].append(txt[:200000])
                (ts_dir / "scripts.json").write_text(json.dumps(scripts, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

            # Save individual card HTML blocks to speed up selector tuning
            try:
                card_nodes = soup.select('div.iva-item-content-fRmzq, div.iva-item-body-oMJBI, div[data-marker="item"], div.iva-item-root')
                if not card_nodes:
                    # Fallback: find titles and capture ancestor blocks
                    anchors = soup.select('a[data-marker="item-title"]')
                    for a_idx, a in enumerate(anchors, start=1):
                        node = a
                        # ascend to a reasonable container
                        for _ in range(8):
                            if node and getattr(node, 'name', None) == 'div' and ('iva-item' in ' '.join(node.get('class', [])) or node.get('data-marker') == 'item'):
                                break
                            node = node.parent
                        target = node or a.parent
                        (ts_dir / "cards" / f"by-title-{a_idx:03d}.html").write_text(str(target), encoding="utf-8")
                else:
                    for idx, node in enumerate(card_nodes, start=1):
                        (ts_dir / "cards" / f"card-{idx:03d}.html").write_text(str(node), encoding="utf-8")
            except Exception:
                pass
        except Exception as exc:
            self.logger.exception("Failed to write HTML to Trash: %s", exc)


def load_sink(spec: str) -> Callable[[str], None]:
    """Load a sink callable from a string like 'pkg.module:function'."""
    if ":" not in spec:
        raise ValueError("Sink spec must be in format 'package.module:function'")
    module_name, func_name = spec.split(":", 1)
    module = importlib.import_module(module_name)
    sink = getattr(module, func_name)
    if not callable(sink):
        raise TypeError("Loaded sink is not callable")
    return sink


