import requests, json
from bs4 import BeautifulSoup
from config.product_config import PRODUCT_CONFIG

def fetch_products():
    query = PRODUCT_CONFIG["query"]
    url = f"https://search.shopping.naver.com/search/all?query={query}"
    res = requests.get(url, headers={"User-Agent":"Mozilla/5.0"})
    soup = BeautifulSoup(res.text, "html.parser")

    script_tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script_tag:
        # ✅ 구조 변경 시 안전하게 빈 결과 반환
        return []

    try:
        data = json.loads(script_tag.string)
        products = data["props"]["pageProps"]["initialState"]["products"]["list"]
    except Exception:
        return []

    parsed = []
    for p in products[:PRODUCT_CONFIG.get("limit", 10)]:
        item = p["item"]
        product_info = {
            "소스": "naver",
            "상품명": item.get("productTitle"),
            "가격": item.get("price"),
            "링크": item.get("link"),
            "고객후기": item.get("reviewCount"),
            "평점": item.get("scoreInfo", {}).get("score")
        }
        parsed.append(product_info)

    return parsed
