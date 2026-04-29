from crawler.naver_fetcher import fetch


def run():
    products = fetch()
    print("총 상품 수:", len(products))


if __name__ == "__main__":
    run()