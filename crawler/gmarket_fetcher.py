import requests
from bs4 import BeautifulSoup
from .saver import save_to_sheet

def fetch():
    url = "https://browse.gmarket.co.kr/search?keyword=여행용%20파우치"
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(url, headers=headers)
    soup = BeautifulSoup(res.text, "html.parser")

    products = []
    items = soup.select("div.box__information")
    for item in items:
        name = item.select_one("span.text__item")
        price = item.select_one("strong.text__value")
        link = item.select_one("a.link__item")

        product = {
            "name": name.get_text(strip=True) if name else None,
            "price": price.get_text(strip=True) if price else None,
            "link": link["href"] if link else None,
        }
        products.append(product)

    print("Fetched products:", products)
    save_to_sheet(products, "Reference1", url)
    return products
