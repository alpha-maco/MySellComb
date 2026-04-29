import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .saver import save_to_sheet

PROFILE_DIR = Path(__file__).resolve().parent / "browser_profile" / "tiktok_chrome"
DEFAULT_LIMIT = 10
DEFAULT_WORKSHEET_NAME = os.environ.get("TIKTOK_WORKSHEET_NAME", "TikTok")
VIDEO_URL_RE = re.compile(r"https://www\.tiktok\.com/@[^/]+/video/\d+")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().strip('"')
    return text or None


def parse_number(value: Any) -> float:
    if value is None:
        return 0

    text = str(value).strip().replace(",", "").upper()
    try:
        if text.endswith("K"):
            return float(text[:-1]) * 1000
        if text.endswith("M"):
            return float(text[:-1]) * 1000000
        if text.endswith("B"):
            return float(text[:-1]) * 1000000000
        return float(text)
    except Exception:
        return 0


def _first_match(patterns: list[re.Pattern], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


def _find_json_value(obj: Any, target_keys: set[str]) -> Any:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in target_keys and value not in (None, "", []):
                return value
            found = _find_json_value(value, target_keys)
            if found not in (None, "", []):
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_json_value(item, target_keys)
            if found not in (None, "", []):
                return found
    return None


def _safe_page_text(page) -> str:
    try:
        return (page.locator("body").inner_text(timeout=2000) or "").lower()
    except Exception:
        return ""


def _is_blocked_page(page) -> str | None:
    try:
        current_url = (page.url or "").lower()
    except Exception:
        current_url = ""

    page_text = _safe_page_text(page)

    url_indicators = ["login", "captcha", "verify", "challenge", "signup"]
    if any(token in current_url for token in url_indicators):
        return f"navigation redirected to blocked page: {current_url}"

    text_indicators = [
        "log in",
        "login",
        "sign up",
        "captcha",
        "security check",
        "verify",
        "too many attempts",
        "something went wrong",
        "try again later",
        "access denied",
        "to continue",
    ]
    for token in text_indicators:
        if token in page_text:
            return f"blocked page keyword detected: {token}"

    return None


def _search_url(keyword: str) -> str:
    return f"https://www.tiktok.com/search/video?q={quote(keyword)}"


def _launch_context(playwright):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        channel="chrome",
        headless=False,
        slow_mo=80,
        viewport={"width": 1440, "height": 1000},
        locale="en-US",
        timezone_id="Asia/Seoul",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/135.0.0.0 Safari/537.36"
        ),
        args=[
            "--disable-blink-features=AutomationControlled",
            "--start-maximized",
            "--lang=en-US",
        ],
    )
    context.set_default_timeout(12000)
    context.set_default_navigation_timeout(30000)
    return context


def _collect_video_links(page) -> list[str]:
    try:
        raw_links = page.locator("a[href*='/video/']").evaluate_all(
            """
            elements => elements
                .map(element => element.href || element.getAttribute('href') || '')
                .filter(Boolean)
            """
        )
    except Exception:
        raw_links = []

    deduped = []
    seen = set()
    for href in raw_links:
        href = href.split("?", 1)[0]
        match = VIDEO_URL_RE.search(href)
        if not match:
            continue
        normalized = match.group(0)
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _scroll_search_results(page, round_index: int):
    page.mouse.move(420 + (round_index % 5) * 45, 280 + (round_index % 4) * 35)
    page.wait_for_timeout(400 + (round_index % 3) * 150)
    page.mouse.wheel(0, 1200 + (round_index % 4) * 280)
    page.wait_for_timeout(900 + (round_index % 3) * 250)


def _discover_video_urls(page, keyword: str, limit: int) -> tuple[list[str], str | None]:
    search_url = _search_url(keyword)
    try:
        page.goto(search_url, wait_until="domcontentloaded", referer="https://www.google.com/", timeout=30000)
    except Exception as exc:
        return [], f"search page load failed: {exc}"

    page.wait_for_timeout(2500)

    blocked_reason = _is_blocked_page(page)
    if blocked_reason:
        return [], blocked_reason

    stable_rounds = 0
    previous_count = 0
    urls: list[str] = []

    for round_index in range(8):
        blocked_reason = _is_blocked_page(page)
        if blocked_reason:
            return urls[:limit], blocked_reason

        urls = _collect_video_links(page)
        if len(urls) >= limit:
            return urls[:limit], None

        if len(urls) == previous_count:
            stable_rounds += 1
        else:
            stable_rounds = 0
            previous_count = len(urls)

        if stable_rounds >= 3:
            break

        _scroll_search_results(page, round_index)

    if not urls:
        return [], "no TikTok video links were found on the search page"

    return urls[:limit], None


def _parse_tiktok_page(page, url: str, keyword: str) -> dict:
    html = page.content()
    title = page.title()

    parsed_ld = None
    scripts = page.locator('script[type="application/ld+json"]')
    for index in range(scripts.count()):
        try:
            raw = scripts.nth(index).inner_text()
            parsed_ld = json.loads(raw)
            break
        except Exception:
            continue

    author = None
    description = None
    publish_date = None
    thumbnail = None

    if parsed_ld:
        author = _find_json_value(parsed_ld, {"author", "creator"})
        if isinstance(author, dict):
            author = author.get("name")
        description = _find_json_value(parsed_ld, {"description", "caption", "name"})
        publish_date = _find_json_value(parsed_ld, {"uploadDate", "datePublished"})
        thumbnail = _find_json_value(parsed_ld, {"thumbnailUrl", "thumbnail"})

    if not author:
        author = _first_match(
            [
                re.compile(r'"authorName":"([^"]+)"'),
                re.compile(r'"nickname":"([^"]+)"'),
                re.compile(r'@([A-Za-z0-9._]+)'),
            ],
            html,
        )

    if not description:
        description = _first_match(
            [
                re.compile(r'"desc":"([^"]+)"'),
                re.compile(r'"description":"([^"]+)"'),
                re.compile(r'<meta name="description" content="([^"]+)"'),
            ],
            html,
        )

    view_count = _first_match(
        [
            re.compile(r'"playCount":("?[\d,\.KMBkmb]+"?)'),
            re.compile(r'"play_count":("?[\d,\.KMBkmb]+"?)'),
            re.compile(r'"viewCount":("?[\d,\.KMBkmb]+"?)'),
        ],
        html,
    )
    like_count = _first_match(
        [
            re.compile(r'"diggCount":("?[\d,\.KMBkmb]+"?)'),
            re.compile(r'"likeCount":("?[\d,\.KMBkmb]+"?)'),
        ],
        html,
    )
    comment_count = _first_match([re.compile(r'"commentCount":("?[\d,\.KMBkmb]+"?)')], html)
    share_count = _first_match([re.compile(r'"shareCount":("?[\d,\.KMBkmb]+"?)')], html)

    return {
        "keyword": keyword,
        "name": _clean(title) or _clean(description) or _clean(url),
        "price": _clean(view_count),
        "link": url,
        "review": _clean(comment_count),
        "rating": _clean(like_count),
        "author": _clean(author),
        "description": _clean(description),
        "publish_date": _clean(publish_date),
        "share_count": _clean(share_count),
        "thumbnail": _clean(thumbnail),
        "view_count": _clean(view_count),
        "like_count": _clean(like_count),
        "comment_count": _clean(comment_count),
        "collected_at": _utc_now_iso(),
    }


def fetch(urls: list[str]) -> list[dict]:
    results = []
    with sync_playwright() as playwright:
        context = _launch_context(playwright)
        page = context.new_page()
        try:
            for url in urls:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2500)
                blocked_reason = _is_blocked_page(page)
                if blocked_reason:
                    print(f"TikTok blocked while fetching {url}: {blocked_reason}")
                    break
                results.append(_parse_tiktok_page(page, url, keyword="manual"))
        finally:
            context.close()
    return results


def fetch_auto(keyword: str, limit: int = DEFAULT_LIMIT) -> dict:
    keyword = keyword.strip()
    limit = max(1, min(int(limit), 30))
    products: list[dict] = []
    blocked_reason = None
    video_urls: list[str] = []

    with sync_playwright() as playwright:
        context = _launch_context(playwright)
        page = context.new_page()

        try:
            video_urls, blocked_reason = _discover_video_urls(page, keyword, limit)

            if not blocked_reason:
                for url in video_urls:
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(2200)
                    except PlaywrightTimeoutError as exc:
                        blocked_reason = f"video page load timeout: {url} / {exc}"
                        break

                    blocked_reason = _is_blocked_page(page)
                    if blocked_reason:
                        blocked_reason = f"{blocked_reason} / url={url}"
                        break

                    item = _parse_tiktok_page(page, url, keyword)
                    item["view_count_num"] = parse_number(item.get("view_count"))
                    products.append(item)
        finally:
            context.close()

    saved_count = 0
    save_error = None
    worksheet_name = DEFAULT_WORKSHEET_NAME

    if products:
        try:
            save_result = save_to_sheet(products, worksheet_name, _search_url(keyword))
            saved_count = save_result["saved_count"]
            worksheet_name = save_result["sheet_name"]
        except Exception as exc:
            save_error = str(exc)

    return {
        "keyword": keyword,
        "products": products,
        "discovered_count": len(video_urls),
        "saved_count": saved_count,
        "sheet_name": worksheet_name,
        "save_error": save_error,
        "blocked_reason": blocked_reason,
    }
