from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from .runtime_paths import get_browser_profile_dir
from .saver import save_to_sheet

SEARCH_QUERY = "여행용 파우치"
PROFILE_DIR = get_browser_profile_dir("coupang_chrome")


def _safe_get_page_text(page):
    try:
        return page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""


def _is_blocked_page(page):
    text = _safe_get_page_text(page)

    blocked_keywords = [
        "Access Denied",
        "403 Forbidden",
        "자동화된 접근",
        "비정상적인 접근",
        "접근이 제한",
        "로봇이 아닙니다",
        "캡차",
    ]

    return any(keyword in text for keyword in blocked_keywords)


def _wait_for_products(page, timeout_ms=15000):
    page.wait_for_selector("li.search-product", timeout=timeout_ms)


def fetch():
    url = f"https://www.coupang.com/np/search?q={SEARCH_QUERY.replace(' ', '+')}"
    products = []

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            slow_mo=120,
            viewport={"width": 1440, "height": 1000},
            locale="ko-KR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/135.0.0.0 Safari/537.36"
            ),
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
            ],
        )

        page = context.new_page()

        try:
            print(f"쿠팡 접속 URL: {url}")

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                print(f"쿠팡 page.goto 실패: {e}")
                return []

            page.wait_for_timeout(3000)

            print(f"현재 URL: {page.url}")

            # 차단 페이지면 종료
            if _is_blocked_page(page):
                print("쿠팡 차단/보안 페이지 감지됨")
                return []

            # 사람처럼 약간 움직이기
            page.mouse.move(500, 300)
            page.wait_for_timeout(700)
            page.mouse.move(800, 420)
            page.wait_for_timeout(700)

            # 상품 목록 대기
            try:
                _wait_for_products(page, timeout_ms=15000)
            except PlaywrightTimeoutError:
                if _is_blocked_page(page):
                    print("쿠팡 차단/보안 페이지 감지됨")
                else:
                    print("쿠팡 상품 리스트 selector를 찾지 못함: li.search-product")
                    print("현재 페이지 텍스트 일부:", _safe_get_page_text(page)[:300])
                return []

            # 스크롤하면서 상품 수 안정화
            last_count = 0
            stable_rounds = 0

            for _ in range(8):
                page.mouse.wheel(0, 1400)
                page.wait_for_timeout(1000)

                try:
                    current_count = page.locator("li.search-product").count()
                except Exception:
                    current_count = last_count

                print(f"현재 쿠팡 아이템 개수: {current_count}")

                if current_count == last_count:
                    stable_rounds += 1
                else:
                    stable_rounds = 0

                last_count = current_count

                if stable_rounds >= 3:
                    break

            items = page.locator("li.search-product")
            count = items.count()

            print("최종 쿠팡 아이템 개수:", count)

            for i in range(count):
                item = items.nth(i)

                try:
                    name = item.locator("div.name").first.inner_text(timeout=2000).strip()
                except Exception:
                    name = None

                try:
                    price = item.locator("strong.price-value").first.inner_text(timeout=2000).strip()
                except Exception:
                    price = None

                try:
                    link_path = item.locator("a.search-product-link").first.get_attribute("href", timeout=2000)
                    link = f"https://www.coupang.com{link_path}" if link_path else None
                except Exception:
                    link = None

                try:
                    review = item.locator("span.rating-total-count").first.inner_text(timeout=1500).strip()
                except Exception:
                    review = None

                try:
                    rating = item.locator("em.rating").first.inner_text(timeout=1500).strip()
                except Exception:
                    rating = None

                if name:
                    products.append({
                        "name": name,
                        "price": price,
                        "link": link,
                        "review": review,
                        "rating": rating,
                    })

            print("Fetched coupang products:", products)

            if products:
                save_to_sheet(products, "Reference1", url)
            else:
                print("저장할 상품이 없습니다.")

            return products

        except Exception as e:
            print(f"쿠팡 fetch 예외 발생: {e}")
            return []

        finally:
            context.close()
