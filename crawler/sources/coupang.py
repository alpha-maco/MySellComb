import requests, json
from bs4 import BeautifulSoup
from config.product_config import PRODUCT_CONFIG
from crawler.parser import parse_products

def fetch_products():
    query = PRODUCT_CONFIG["query"]
    url = f"https://search.shopping.naver.com/search/all?query={query}"
    res = requests.get(url, headers={"User-Agent":"Mozilla/5.0"})
    soup = BeautifulSoup(res.text, "html.parser")

    # HTML 내 JSON 데이터 추출
    script_tag = soup.find("script", {"id": "__NEXT_DATA__"})
    data = json.loads(script_tag.string)

    products = data["props"]["pageProps"]["initialState"]["products"]["list"]

    # 상품명 + 가격 + 링크 + 고객후기 + 평점
    parsed = []
    for p in products[:PRODUCT_CONFIG.get("limit", 10)]:
        item = p["item"]
        product_info = {
            "상품명": item.get("productTitle"),
            "가격": item.get("price"),
            "링크": item.get("link"),
            "고객후기": item.get("reviewCount")
        }
        if "scoreInfo" in item:
            product_info["평점"] = item["scoreInfo"].get("score")
        parsed.append(product_info)

    return parsed
