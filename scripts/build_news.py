#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
地域ニュースマップ data/news.json 生成スクリプト v18

できること：
1. data/manual-news.json の手動ニュースを読み込む
2. data/sources.json の rss_sources から enabled:true のRSSを取得する
3. data/geocode-cache.json の地名キャッシュで座標を付ける
4. 全国向けニュースは map:false にして地図非表示
5. data/news.json を生成する

注意：
- RSSの自動分類はまだ簡易版です
- 座標が取れないRSSニュースはニュース一覧のみ表示します
- 事件・防犯系はピンポイント表示を避け、原則 area 表示にします
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

MANUAL = DATA / "manual-news.json"
SOURCES = DATA / "sources.json"
CACHE = DATA / "geocode-cache.json"
OUT = DATA / "news.json"

JST = timezone(timedelta(hours=9))

VALID_CATEGORIES = {"crime", "event", "traffic"}

REQUIRED = {
    "id",
    "category",
    "title",
    "summary",
    "area",
    "prefecture",
    "time",
    "precision",
    "displayType",
    "url",
    "source",
}


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def short_id(prefix: str, text: str) -> str:
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{h}"


def is_national(item: dict[str, Any]) -> bool:
    text = " ".join(str(item.get(k, "")) for k in ("title", "summary", "area", "precision", "prefecture"))
    return (
        item.get("precision") == "全国向け情報"
        or item.get("area") == "全国"
        or item.get("prefecture") == "全国"
        or "全国向け" in text
    )


def apply_display_rules(item: dict[str, Any]) -> dict[str, Any]:
    """カテゴリごとの地図表示ルールを付与する。"""
    item = dict(item)
    now = datetime.now(JST).isoformat(timespec="seconds")
    cat = item.get("category")

    if cat == "event":
        if item.get("sourceLink") or item.get("alwaysShow"):
            item["alwaysShow"] = True
            item["mapRule"] = "always"
        else:
            item["mapRule"] = "event_period"

    elif cat == "crime":
        item["mapRule"] = "crime_24h"
        item.setdefault("displayHours", 24)
        item.setdefault("publishedAt", now)

    elif cat == "traffic":
        if item.get("map") is False or item.get("displayType") == "none" or item.get("precision") == "全国向け情報":
            item["mapRule"] = "link_only"
            item["map"] = False
            item["displayType"] = "none"
        else:
            item["mapRule"] = "traffic_6h"
            item.setdefault("displayHours", 6)
            item.setdefault("publishedAt", now)

    return item


def is_fake_event_link(item: dict[str, Any]) -> bool:
    """イベント確認リンクの水増しデータはイベントとして扱わない。"""
    if item.get("category") != "event":
        return False
    title = str(item.get("title", ""))
    source = str(item.get("source", ""))
    precision = str(item.get("precision", ""))
    return (
        item.get("sourceLink") is True
        or "イベント情報確認リンク" in title
        or "イベント確認リンク" in title
        or source == "イベント情報リンク"
        or (precision == "確認リンク" and "イベント" in title)
    )


def normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    item = dict(item)
    item = apply_display_rules(item)

    missing = sorted(REQUIRED - set(item))
    if missing:
        raise ValueError(f"missing keys in item id={item.get('id')}: {missing}")

    if item["category"] not in VALID_CATEGORIES:
        raise ValueError(f"invalid category id={item.get('id')}: {item['category']}")

    if is_national(item):
        item["map"] = False
        item["displayType"] = "none"

    if item.get("map") is not False and item.get("displayType") != "none":
        if item.get("displayType") == "line":
            if not item.get("points"):
                raise ValueError(f"line item requires points id={item.get('id')}")
        else:
            if not isinstance(item.get("lat"), (int, float)) or not isinstance(item.get("lon"), (int, float)):
                # 座標なしは一覧のみへ落とす
                item["map"] = False
                item["displayType"] = "none"
                item["precision"] = item.get("precision") or "位置未確認"

    return item


def fetch_url(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "local-news-map/0.1 (+https://github.com/)"
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def find_child_text(elem: ET.Element, names: tuple[str, ...]) -> str:
    # RSS/Atom namespaces are ignored by checking tag suffix
    for child in elem.iter():
        tag = child.tag.split("}")[-1].lower()
        if tag in names:
            return clean_text(child.text or "")
    return ""


def parse_rss_items(xml_bytes: bytes) -> list[dict[str, str]]:
    root = ET.fromstring(xml_bytes)
    items: list[dict[str, str]] = []

    # RSS item
    for item in root.iter():
        tag = item.tag.split("}")[-1].lower()
        if tag not in {"item", "entry"}:
            continue

        title = find_child_text(item, ("title",))
        link = find_child_text(item, ("link",))
        summary = find_child_text(item, ("description", "summary", "content"))
        published = find_child_text(item, ("pubdate", "published", "updated"))

        # Atom link may be attribute href
        if not link:
            for child in item.iter():
                ctag = child.tag.split("}")[-1].lower()
                if ctag == "link" and child.attrib.get("href"):
                    link = child.attrib["href"]
                    break

        if title:
            items.append({
                "title": title,
                "summary": summary or title,
                "url": link or "#",
                "time": published or datetime.now(JST).strftime("%Y/%m/%d"),
            })

    return items


def lookup_place(text: str, default_place: str, cache_items: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    # 1. title/summary内にキャッシュキーが含まれていれば採用
    for key, value in cache_items.items():
        if key and key in text:
            return value, key

    # 2. default_place
    if default_place and default_place in cache_items:
        return cache_items[default_place], default_place

    return None, None


def make_item_from_rss(source: dict[str, Any], raw: dict[str, str], cache_items: dict[str, Any]) -> dict[str, Any]:
    category = source.get("category", "event")
    if category not in VALID_CATEGORIES:
        category = "event"

    title = raw["title"]
    summary = raw.get("summary") or title
    text = f"{title} {summary}"

    place, place_name = lookup_place(text, source.get("default_place", ""), cache_items)

    display_type = "pin" if category == "event" else "area"
    precision = "位置未確認"
    lat = lon = None

    if place:
        lat = place.get("lat")
        lon = place.get("lon")
        precision = place.get("precision", "キャッシュ座標")
        if category == "crime":
            display_type = "area"
        elif category == "traffic":
            display_type = "area"

    item: dict[str, Any] = {
        "id": short_id("rss", raw.get("url", "") + title),
        "category": category,
        "title": title,
        "summary": summary[:180],
        "area": place_name or source.get("area", source.get("prefecture", "全国")),
        "prefecture": source.get("prefecture", "全国"),
        "time": raw.get("time") or datetime.now(JST).strftime("%Y/%m/%d"),
        "precision": precision,
        "displayType": display_type if place else "none",
        "url": raw.get("url", "#"),
        "source": source.get("name", "RSS"),
    }

    if place and isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        item["lat"] = lat
        item["lon"] = lon
        if category in {"crime", "traffic"}:
            item["radius"] = 1200
    else:
        item["map"] = False
        item["displayType"] = "none"

    return item


def load_manual_items() -> list[dict[str, Any]]:
    raw = load_json(MANUAL, {"items": []})
    items = raw.get("items", [])
    if not isinstance(items, list):
        raise ValueError("manual-news.json items must be a list")
    return items


def load_rss_items() -> list[dict[str, Any]]:
    sources = load_json(SOURCES, {"rss_sources": []})
    cache = load_json(CACHE, {"items": {}})
    cache_items = cache.get("items", {})

    out: list[dict[str, Any]] = []
    for source in sources.get("rss_sources", []):
        if not source.get("enabled"):
            continue

        url = source.get("url")
        if not url:
            continue

        try:
            xml_bytes = fetch_url(url)
            raw_items = parse_rss_items(xml_bytes)
            max_items = int(source.get("max_items", 10))
            for raw in raw_items[:max_items]:
                out.append(make_item_from_rss(source, raw, cache_items))
            print(f"fetched RSS: {source.get('name')} {len(raw_items[:max_items])} items", file=sys.stderr)
        except Exception as e:
            print(f"RSS fetch failed: {source.get('name')} {e}", file=sys.stderr)

    return out


def print_event_coverage(items):
    prefs = {}
    for item in items:
        if item.get("category") != "event":
            continue
        pref = item.get("prefecture", "不明")
        prefs[pref] = prefs.get(pref, 0) + 1

    target = 5
    low = {k: v for k, v in prefs.items() if v < target}
    print("event coverage:")
    for k in sorted(prefs):
        print(f"  {k}: {prefs[k]} events")
    if low:
        print("prefectures below target:")
        for k, v in sorted(low.items()):
            print(f"  {k}: {v}/{target}")


def main() -> None:
    manual = load_manual_items()
    rss = load_rss_items()

    normalized = [normalize_item(item) for item in [*manual, *rss] if not is_fake_event_link(item)]

    seen = set()
    unique: list[dict[str, Any]] = []
    for item in normalized:
        item_id = str(item["id"])
        if item_id in seen:
            continue
        seen.add(item_id)
        unique.append(item)

    out = {
        "updated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "source_note": "manual-news.json と enabled RSS から生成。全国向けニュースは地図非表示。",
        "items": unique,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} ({len(unique)} items)")
    print_event_coverage(unique)


if __name__ == "__main__":
    main()
