from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from .runtime_paths import get_browser_profile_dir
from .saver import save_to_sheet

SEARCH_QUERY = "여행용 파우치"
PROFILE_DIR = get_browser_profile_dir("naver_chrome")


def _safe_get_page_text(page):
    try:
        return page.locator("body").inner_text(timeout=2000)
    except Exception:
        return ""


def _is_security_page(page):
    try:
        current_url = page.url or ""
    except Exception:
        current_url = ""

    text = _safe_get_page_text(page)

    keywords = [
        "보안 확인",
        "제한된 접근",
        "스팸을 방지",
        "실제 사용자인지 확인",
        "자동화된 접근",
    ]

    if "nid.naver.com" in current_url:
        return True

    return any(keyword in text for keyword in keywords)


def _is_blocked_page(page):
    """
    네이버 쇼핑 자체가 일시적으로 접속 제한한 페이지인지 확인
    """
    text = _safe_get_page_text(page)

    blocked_keywords = [
        "쇼핑 서비스 접속이 일시적으로 제한되었습니다",
        "비정상적인 접근이 감지된 경우",
        "짧은 시간 내에 너무 많은 요청",
        "해당 네트워크의 접속을 일시적으로 제한",
    ]

    return any(keyword in text for keyword in blocked_keywords)


def _wait_for_products(page, timeout_ms=12000):
    page.wait_for_selector("div[class*='basicList_item']", timeout=timeout_ms)


def fetch():
    url = f"https://search.shopping.naver.com/search/all?query={SEARCH_QUERY}"

    products = []
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            slow_mo=150,
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
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            # 1) 보안 확인 페이지 대응
            if _is_security_page(page):
                print("보안 확인 필요: 직접 통과하세요 (최대 90초 대기)")
                solved = False

                for _ in range(90):
                    page.wait_for_timeout(1000)

                    if _is_security_page(page):
                        continue

                    if _is_blocked_page(page):
                        print("네이버 쇼핑 접속 제한 페이지 감지됨")
                        return []

                    try:
                        _wait_for_products(page, timeout_ms=3000)
                        solved = True
                        break
                    except PlaywrightTimeoutError:
                        continue

                if not solved:
                    print("보안 확인 실패 또는 상품 로딩 실패")
                    return []

            # 2) 이미 제한 페이지인지 바로 확인
            if _is_blocked_page(page):
                print("네이버 쇼핑 접속 제한 페이지 감지됨")
                return []

            # 3) 상품 리스트 로딩 대기
            try:
                _wait_for_products(page, timeout_ms=12000)
            except PlaywrightTimeoutError:
                if _is_blocked_page(page):
                    print("네이버 쇼핑 접속 제한 페이지 감지됨")
                else:
                    print("상품 리스트 못 찾음")
                return []

            # 4) 사람처럼 약간 움직이고 스크롤
            page.mouse.move(500, 300)
            page.wait_for_timeout(800)

            last_count = 0
            stable_rounds = 0

            for _ in range(10):
                page.mouse.wheel(0, 1500)
                page.wait_for_timeout(1200)

                current_count = page.locator("div[class*='basicList_item']").count()

                if current_count == last_count:
                    stable_rounds += 1
                else:
                    stable_rounds = 0

                last_count = current_count

                if stable_rounds >= 3:
                    break

            items = page.locator("div[class*='basicList_item']")
            count = items.count()

            print("아이템 개수:", count)

            for i in range(count):
                item = items.nth(i)

                try:
                    name_el = item.locator("a[class*='basicList_link']").first
                    name = name_el.inner_text(timeout=3000).strip()
                except Exception:
                    name = None

                try:
                    link = name_el.get_attribute("href") if name else None
                except Exception:
                    link = None

                try:
                    price = item.locator("span[class*='price_num']").first.inner_text(timeout=3000).strip()
                except Exception:
                    price = None

                try:
                    review = item.locator("span[class*='basicList_num']").first.inner_text(timeout=2000).strip()
                except Exception:
                    review = None

                try:
                    rating = item.locator("span[class*='basicList_star']").first.inner_text(timeout=2000).strip()
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

            print("Fetched products:", products)

            if products:
                save_to_sheet(products, "Reference1", url)
            else:
                print("저장할 상품이 없습니다.")

            return products

        finally:
            context.close()
