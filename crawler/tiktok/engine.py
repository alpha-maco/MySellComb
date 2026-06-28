import re
from .selectors import VIEW_KEYS, LIKE_KEYS, COMMENT_KEYS, SHARE_KEYS


def _first_match(keys, html):
    for key in keys:
        match = re.search(rf'"{key}":("?[\d,\.KMBkmb]+"?)', html)
        if match:
            return match.group(1).strip('"')
    return None


def parse_video(page, url):
    html = page.content()
    title = page.title()

    view = _first_match(VIEW_KEYS, html)
    like = _first_match(LIKE_KEYS, html)
    comment = _first_match(COMMENT_KEYS, html)
    share = _first_match(SHARE_KEYS, html)

    return {
        "name": title,
        "price": view,      # 조회수
        "link": url,
        "review": comment,  # 댓글수
        "rating": like,     # 좋아요수
        "view_count": view,
        "like_count": like,
        "comment_count": comment,
        "share_count": share,
    }