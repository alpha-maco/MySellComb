from bs4 import BeautifulSoup


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split())
    return text if text else None


def parse_coupang_product_fragment(html_fragment: str) -> dict:
    soup = BeautifulSoup(html_fragment, "html.parser")

    root = soup.select_one("a.impression-logged.view-logged") or soup

    # 링크
    href = root.get("href") if root else None
    if href and href.startswith("/"):
        link = f"https://www.coupang.com{href}"
    else:
        link = href

    # 상품명
    name = None
    name_node = soup.select_one("div.ProductUnit_productNameV2__cV9cw")
    if name_node:
        name = _clean_text(name_node.get_text(" ", strip=True))

    # 정상가 / 판매가 / 할인율
    original_price = None
    original_price_node = soup.select_one("del")
    if original_price_node:
        original_price = _clean_text(original_price_node.get_text(strip=True))

    sale_price = None
    sale_price_node = soup.select_one("div.fw-text-[20px]/\\[24px\\] span")
    if sale_price_node:
        sale_price = _clean_text(sale_price_node.get_text(strip=True))
    if not sale_price:
        # fallback
        price_candidates = soup.select("span")
        for node in price_candidates:
            text = _clean_text(node.get_text(strip=True))
            if text and text.endswith("원"):
                sale_price = text
                break

    discount_rate = None
    discount_node = soup.find(string=lambda s: s and "%" in s)
    if discount_node:
        discount_rate = _clean_text(str(discount_node))

    # 리뷰 / 평점
    review_count = None
    review_node = soup.select_one("div.fw-text-\\[12px\\].fw-leading-\\[14px\\].fw-font-\\[400\\] span")
    if review_node:
        review_text = _clean_text(review_node.get_text(strip=True))
        if review_text:
            review_count = review_text.strip("()")

    rating = None
    rating_node = soup.select_one("div[aria-label]")
    if rating_node:
        rating = _clean_text(rating_node.get("aria-label"))

    # 배송/배지
    badges = []
    for text_node in soup.find_all(string=True):
        text = _clean_text(str(text_node))
        if not text:
            continue
        if text in {"무료배송", "내일(금)", "도착 보장"}:
            badges.append(text)

    shipping_info = " ".join(dict.fromkeys(badges)) if badges else None

    # 광고 여부
    is_ad = bool(soup.select_one("span.AdMark_text__Rp7px"))

    # 이미지
    image_url = None
    image_node = soup.select_one("figure img")
    if image_node:
        image_url = image_node.get("src")

    return {
        "name": name,
        "price": sale_price,
        "original_price": original_price,
        "discount_rate": discount_rate,
        "link": link,
        "review": review_count,
        "rating": rating,
        "shipping_info": shipping_info,
        "is_ad": is_ad,
        "image_url": image_url,
    }


def parse_coupang_product_fragments(html_fragments: list[str]) -> list[dict]:
    results = []
    for fragment in html_fragments:
        parsed = parse_coupang_product_fragment(fragment)
        if parsed.get("name"):
            results.append(parsed)
    return results


def parse_products(products, source="naver"):
    result = []

    for p in products:
        if source in ("naver", "coupang"):
            product_info = {
                "상품명": p.get("name"),
                "가격": p.get("price"),
                "링크": p.get("link"),
                "고객후기": p.get("review"),
                "평점": p.get("rating"),
            }
        else:
            product_info = {"상품명": "지원하지 않는 소스"}

        result.append(product_info)

    return result