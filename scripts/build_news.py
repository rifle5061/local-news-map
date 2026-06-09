#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
地域ニュースマップ data/news.json 生成スクリプト

現段階：
- data/manual-news.json を読み込み
- 全国向けニュースは map:false / displayType:none に補正
- 必須項目を軽くチェック
- data/news.json を生成

次段階：
- data/sources.json の rss_sources を読み込んでRSS取得
- タイトル/本文から地名抽出
- geocode_cache.json で緯度経度付与
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
MANUAL = DATA / "manual-news.json"
OUT = DATA / "news.json"

JST = timezone(timedelta(hours=9))


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


def is_national(item: dict[str, Any]) -> bool:
    text = " ".join(str(item.get(k, "")) for k in ("title", "summary", "area", "precision"))
    return (
        item.get("precision") == "全国向け情報"
        or item.get("area") == "全国"
        or "全国向け" in text
        or item.get("prefecture") == "全国"
    )


def normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    item = dict(item)

    missing = sorted(REQUIRED - set(item))
    if missing:
        raise ValueError(f"missing keys in item id={item.get('id')}: {missing}")

    if item["category"] not in {"crime", "event", "traffic"}:
        raise ValueError(f"invalid category id={item.get('id')}: {item['category']}")

    # 全国向けニュースは地図に出さない
    if is_national(item):
        item["map"] = False
        item["displayType"] = "none"

    # 地図表示するデータだけ座標を要求
    if item.get("map") is not False and item.get("displayType") != "none":
        if item.get("displayType") == "line":
            if not item.get("points"):
                raise ValueError(f"line item requires points id={item.get('id')}")
        else:
            if not isinstance(item.get("lat"), (int, float)) or not isinstance(item.get("lon"), (int, float)):
                raise ValueError(f"map item requires lat/lon id={item.get('id')}")

    return item


def main() -> None:
    if not MANUAL.exists():
        raise FileNotFoundError(f"{MANUAL} not found")

    raw = json.loads(MANUAL.read_text(encoding="utf-8"))
    items = raw.get("items", [])
    if not isinstance(items, list):
        raise ValueError("manual-news.json items must be a list")

    normalized = [normalize_item(item) for item in items]

    # id重複チェック
    ids = [item["id"] for item in normalized]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate news id found")

    out = {
        "updated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "source_note": "manual-news.json から生成。全国向けニュースはニュース一覧のみ表示し、地図上には表示しません。",
        "items": normalized,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} ({len(normalized)} items)")


if __name__ == "__main__":
    main()
