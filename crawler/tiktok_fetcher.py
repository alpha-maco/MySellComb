import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .runtime_paths import get_browser_profile_dir
from .saver import save_to_sheet

PROFILE_DIR = get_browser_profile_dir("tiktok_chrome")
AUTOMATION_PROFILE_DIR = get_browser_profile_dir("tiktok_chrome_automation")
DEFAULT_LIMIT = 10
DISCOVERY_MULTIPLIER = 2
DISCOVERY_BUFFER = 4
MAX_DISCOVERY_LIMIT = 60
DEFAULT_WORKSHEET_NAME = os.environ.get("TIKTOK_WORKSHEET_NAME", "TikTok")
VIDEO_URL_RE = re.compile(r"https://www\.tiktok\.com/@[^/]+/video/\d+")
TIKTOK_ORIGINS = ["https://www.tiktok.com", "https://www.tiktokv.com"]
AUTH_COOKIE_EXACT_NAMES = {
    "sessionid",
    "sessionid_ss",
    "sid_tt",
    "sid_guard",
    "uid_tt",
    "uid_tt_ss",
    "sid_ucp_v1",
    "ssid_ucp_v1",
    "passport_auth_status",
    "passport_auth_status_ss",
}
AUTH_COOKIE_PREFIXES = (
    "sessionid",
    "uid_tt",
    "passport_auth_status",
)


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


def _page_has_video_content(page) -> bool:
    try:
        current_url = page.url or ""
    except Exception:
        current_url = ""

    if VIDEO_URL_RE.search(current_url):
        try:
            if page.locator('script[type="application/ld+json"]').count() > 0:
                return True
        except Exception:
            pass

    try:
        if _collect_video_links(page):
            return True
    except Exception:
        pass

    try:
        html = (page.content() or "").lower()
    except Exception:
        html = ""

    content_markers = [
        '"playcount"',
        '"diggcount"',
        '"commentcount"',
        '"sharecount"',
        '"iteminfos"',
        '"iteminfo"',
    ]
    return any(marker in html for marker in content_markers)


def _is_blocked_page(page) -> str | None:
    try:
        current_url = (page.url or "").lower()
    except Exception:
        current_url = ""

    page_text = _safe_page_text(page)
    has_video_content = _page_has_video_content(page)

    url_indicators = ["login", "captcha", "verify", "challenge", "signup"]
    if any(token in current_url for token in url_indicators):
        return f"navigation redirected to blocked page: {current_url}"

    hard_text_indicators = [
        "captcha",
        "security check",
        "verify",
        "too many attempts",
        "something went wrong",
        "access denied",
        "try again later",
    ]
    for token in hard_text_indicators:
        if token in page_text:
            return f"blocked page keyword detected: {token}"

    if has_video_content:
        return None

    soft_text_indicators = [
        "log in",
        "login",
        "sign up",
        "to continue",
    ]
    for token in soft_text_indicators:
        if token in page_text:
            return f"blocked page keyword detected: {token}"

    return None


def _is_soft_block_reason(reason: str | None) -> bool:
    if not reason:
        return False

    soft_tokens = [
        "blocked page keyword detected: log in",
        "blocked page keyword detected: login",
        "blocked page keyword detected: sign up",
        "blocked page keyword detected: to continue",
        "blocked page keyword detected: something went wrong",
        "blocked page keyword detected: try again later",
    ]
    return any(token in reason for token in soft_tokens)


def _detect_blocked_page(page, retries: int = 2) -> str | None:
    blocked_reason = None
    for attempt in range(retries + 1):
        blocked_reason = _is_blocked_page(page)
        if not blocked_reason:
            return None
        if not _is_soft_block_reason(blocked_reason):
            return blocked_reason
        if attempt < retries:
            page.wait_for_timeout(1500 + (attempt * 1000))
    return blocked_reason


def _search_url(keyword: str) -> str:
    return f"https://www.tiktok.com/search/video?q={quote(keyword)}"


def _normalize_video_url(value: str) -> str | None:
    if not value:
        return None

    cleaned = str(value).strip()
    match = VIDEO_URL_RE.search(cleaned)
    if not match:
        return None
    return match.group(0)


def _dedupe_video_urls(urls: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()

    for url in urls:
        normalized = _normalize_video_url(url)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)

    return deduped


def _calculate_discovery_limit(save_target_count: int) -> int:
    return min(
        MAX_DISCOVERY_LIMIT,
        max(save_target_count * DISCOVERY_MULTIPLIER, save_target_count + DISCOVERY_BUFFER),
    )


def _is_auth_cookie_name(name: str) -> bool:
    lowered = (name or "").strip().lower()
    if lowered in AUTH_COOKIE_EXACT_NAMES:
        return True
    return any(lowered.startswith(prefix) for prefix in AUTH_COOKIE_PREFIXES)


def _inspect_tiktok_session(context) -> dict:
    try:
        cookies = context.cookies(TIKTOK_ORIGINS)
    except Exception:
        cookies = []

    cookie_names = sorted({cookie.get("name", "") for cookie in cookies if cookie.get("name")})
    auth_cookie_names = [name for name in cookie_names if _is_auth_cookie_name(name)]
    login_state = "logged_in" if auth_cookie_names else "logged_out"

    return {
        "login_state": login_state,
        "cookie_names": cookie_names,
        "auth_cookie_names": auth_cookie_names,
    }


def _remove_auth_cookies(context, auth_cookie_names: list[str]) -> list[str]:
    if not auth_cookie_names:
        return []

    removed_names: list[str] = []
    try:
        for name in auth_cookie_names:
            context.clear_cookies(name=name)
            removed_names.append(name)
        return removed_names
    except TypeError:
        pass
    except Exception:
        return removed_names

    try:
        existing_cookies = context.cookies(TIKTOK_ORIGINS)
        context.clear_cookies()
        safe_cookies = [
            cookie
            for cookie in existing_cookies
            if not _is_auth_cookie_name(cookie.get("name", ""))
        ]
        if safe_cookies:
            context.add_cookies(safe_cookies)
        return list(auth_cookie_names)
    except Exception:
        return removed_names


def _ensure_logged_out_session(context) -> dict:
    before = _inspect_tiktok_session(context)
    removed_auth_cookie_names = _remove_auth_cookies(context, before["auth_cookie_names"])
    after = _inspect_tiktok_session(context)

    if before["login_state"] == "logged_out":
        login_state_reason = "no auth cookies detected"
    elif after["login_state"] == "logged_out":
        login_state_reason = "auth cookies removed"
    else:
        login_state_reason = "auth cookies still present"

    return {
        "login_state": after["login_state"],
        "login_state_before": before["login_state"],
        "login_state_reason": login_state_reason,
        "auth_cookie_names": after["auth_cookie_names"],
        "removed_auth_cookie_names": removed_auth_cookie_names,
    }


def _resolve_profile_dir(profile_mode: str) -> Path:
    return AUTOMATION_PROFILE_DIR if profile_mode == "automation" else PROFILE_DIR


def _clear_profile_lockfiles(profile_dir: Path) -> None:
    for pattern in ("lockfile", "Singleton*", "*.lock", "*.LOCK"):
        for path in profile_dir.glob(pattern):
            try:
                if path.is_file() or path.is_symlink():
                    path.unlink(missing_ok=True)
            except Exception:
                continue


def _reset_profile_dir(profile_dir: Path) -> None:
    if not profile_dir.exists():
        profile_dir.mkdir(parents=True, exist_ok=True)
        return

    stale_dir = profile_dir.with_name(
        f"{profile_dir.name}_stale_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    )

    try:
        profile_dir.rename(stale_dir)
    except Exception:
        try:
            shutil.rmtree(profile_dir, ignore_errors=True)
        except Exception:
            return

    profile_dir.mkdir(parents=True, exist_ok=True)


def _prune_stale_runtime_profiles(runtime_root: Path, max_age_hours: int = 12) -> None:
    cutoff = datetime.now().timestamp() - (max_age_hours * 60 * 60)
    for path in runtime_root.iterdir():
        try:
            if path.is_dir() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except Exception:
            continue


def _create_automation_runtime_profile(base_profile_dir: Path) -> Path:
    runtime_root = base_profile_dir.parent / f"{base_profile_dir.name}_runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    _prune_stale_runtime_profiles(runtime_root)
    runtime_profile_dir = Path(tempfile.mkdtemp(prefix="run_", dir=runtime_root))

    for child in base_profile_dir.iterdir():
        target = runtime_profile_dir / child.name
        try:
            if child.is_dir():
                shutil.copytree(child, target, dirs_exist_ok=True)
            elif child.is_file():
                shutil.copy2(child, target)
        except Exception:
            continue

    _clear_profile_lockfiles(runtime_profile_dir)
    return runtime_profile_dir


def _launch_context(playwright, profile_mode: str = "default"):
    base_profile_dir = _resolve_profile_dir(profile_mode)
    base_profile_dir.mkdir(parents=True, exist_ok=True)
    launch_kwargs = {
        "user_data_dir": str(base_profile_dir),
        "channel": "chrome",
        "headless": False,
        "slow_mo": 80,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--start-maximized",
            "--lang=en-US",
        ],
        "locale": "en-US",
        "timezone_id": "Asia/Seoul",
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/135.0.0.0 Safari/537.36"
        ),
    }

    last_error = None
    for attempt in range(2):
        profile_dir = base_profile_dir
        if profile_mode == "automation":
            # Automation runs do not rely on persisted login state, so launch
            # them with a fresh runtime profile to avoid lock/corruption issues.
            profile_dir = _create_automation_runtime_profile(base_profile_dir)
            launch_kwargs["user_data_dir"] = str(profile_dir)
        elif attempt == 0:
            _clear_profile_lockfiles(profile_dir)
        else:
            _reset_profile_dir(profile_dir)
        try:
            context = playwright.chromium.launch_persistent_context(**launch_kwargs)
            break
        except Exception as exc:
            last_error = exc
    else:
        raise last_error

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


def _sort_products_by_view_count(products: list[dict]) -> list[dict]:
    return sorted(
        products,
        key=lambda product: parse_number(product.get("view_count")),
        reverse=True,
    )


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

    blocked_reason = _detect_blocked_page(page, retries=2)
    if blocked_reason:
        return [], blocked_reason

    stable_rounds = 0
    previous_count = 0
    urls: list[str] = []

    for round_index in range(8):
        blocked_reason = _detect_blocked_page(page, retries=1)
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


def _discover_video_urls_from_seed(page, seed_url: str, limit: int) -> tuple[list[str], str | None]:
    normalized_seed_url = _normalize_video_url(seed_url)
    if not normalized_seed_url:
        return [], "invalid TikTok video URL"

    try:
        page.goto(normalized_seed_url, wait_until="domcontentloaded", referer="https://www.google.com/", timeout=30000)
    except Exception as exc:
        return [], f"seed video page load failed: {exc}"

    page.wait_for_timeout(2500)

    blocked_reason = _detect_blocked_page(page, retries=2)
    if blocked_reason:
        return [normalized_seed_url], blocked_reason

    stable_rounds = 0
    previous_count = 0
    urls: list[str] = [normalized_seed_url]

    for round_index in range(8):
        blocked_reason = _detect_blocked_page(page, retries=1)
        if blocked_reason:
            return urls[:limit], blocked_reason

        current_urls = _dedupe_video_urls([normalized_seed_url, *_collect_video_links(page)])
        if len(current_urls) >= limit:
            return current_urls[:limit], None

        if len(current_urls) == previous_count:
            stable_rounds += 1
        else:
            stable_rounds = 0
            previous_count = len(current_urls)
            urls = current_urls

        if stable_rounds >= 3:
            break

        _scroll_search_results(page, round_index)

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


def fetch(urls: list[str], profile_mode: str = "default") -> list[dict]:
    results = []
    with sync_playwright() as playwright:
        context = _launch_context(playwright, profile_mode=profile_mode)
        page = context.new_page()
        try:
            session_info = _ensure_logged_out_session(context)
            if session_info["login_state"] != "logged_out":
                print("TikTok login session is active and could not be cleared.")
                return []
            for url in urls:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2500)
                blocked_reason = _detect_blocked_page(page, retries=1)
                if blocked_reason:
                    print(f"TikTok blocked while fetching {url}: {blocked_reason}")
                    break
                results.append(_parse_tiktok_page(page, url, keyword="manual"))
        finally:
            context.close()
    return _sort_products_by_view_count(results)


def fetch_auto(
    query: str,
    limit: int = DEFAULT_LIMIT,
    input_mode: str = "keyword",
    save_mode: str = "overwrite",
    profile_mode: str = "default",
) -> dict:
    query = query.strip()
    input_mode = (input_mode or "keyword").strip().lower()
    save_mode = (save_mode or "overwrite").strip().lower()
    if save_mode not in {"overwrite", "append"}:
        save_mode = "overwrite"
    save_target_count = max(1, min(int(limit), 30))
    discovery_limit = _calculate_discovery_limit(save_target_count)
    products: list[dict] = []
    blocked_reason = None
    fetch_warnings: list[str] = []
    video_urls: list[str] = []
    session_info = {
        "login_state": "unknown",
        "login_state_before": "unknown",
        "login_state_reason": "session check not started",
        "auth_cookie_names": [],
        "removed_auth_cookie_names": [],
    }
    input_value = query
    source_url = query

    with sync_playwright() as playwright:
        context = _launch_context(playwright, profile_mode=profile_mode)
        page = context.new_page()

        try:
            session_info = _ensure_logged_out_session(context)
            if session_info["login_state"] != "logged_out":
                blocked_reason = "TikTok login session is active and could not be cleared."
            elif input_mode == "url":
                normalized_seed_url = _normalize_video_url(query)
                if not normalized_seed_url:
                    blocked_reason = "invalid TikTok video URL"
                else:
                    input_value = normalized_seed_url
                    source_url = normalized_seed_url
                    video_urls, blocked_reason = _discover_video_urls_from_seed(page, normalized_seed_url, discovery_limit)
            else:
                input_mode = "keyword"
                source_url = _search_url(query)
                video_urls, blocked_reason = _discover_video_urls(page, query, discovery_limit)

            if not blocked_reason:
                for url in video_urls:
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(2200)
                    except PlaywrightTimeoutError as exc:
                        fetch_warnings.append(f"video page load timeout: {url} / {exc}")
                        continue

                    page_blocked_reason = _detect_blocked_page(page, retries=1)
                    if page_blocked_reason:
                        fetch_warnings.append(f"{page_blocked_reason} / url={url}")
                        continue

                    item = _parse_tiktok_page(page, url, input_value)
                    item["view_count_num"] = parse_number(item.get("view_count"))
                    products.append(item)
        finally:
            context.close()

    products = _sort_products_by_view_count(products)
    products_to_save = []
    for product in products[:save_target_count]:
        product_to_save = dict(product)
        product_to_save["_save_mode"] = save_mode
        products_to_save.append(product_to_save)
    saved_count = 0
    save_error = None
    worksheet_name = DEFAULT_WORKSHEET_NAME

    if products_to_save:
        try:
            save_result = save_to_sheet(products_to_save, worksheet_name, source_url)
            saved_count = save_result["saved_count"]
            worksheet_name = save_result["sheet_name"]
        except Exception as exc:
            save_error = str(exc)

    if not blocked_reason and not products and fetch_warnings:
        blocked_reason = fetch_warnings[0]

    return {
        "keyword": input_value,
        "input_value": input_value,
        "input_mode": input_mode,
        "save_mode": save_mode,
        "save_target_count": save_target_count,
        "discovery_limit": discovery_limit,
        "products": products,
        "discovered_count": len(video_urls),
        "saved_count": saved_count,
        "sheet_name": worksheet_name,
        "save_error": save_error,
        "blocked_reason": blocked_reason,
        "fetch_warning_count": len(fetch_warnings),
        "fetch_warning_samples": fetch_warnings[:5],
        "source_url": source_url,
        "profile_mode": profile_mode,
        "login_state": session_info["login_state"],
        "login_state_before": session_info["login_state_before"],
        "login_state_reason": session_info["login_state_reason"],
        "auth_cookie_names": session_info["auth_cookie_names"],
        "removed_auth_cookie_names": session_info["removed_auth_cookie_names"],
    }
