def parse_views(views_str: str) -> int:
    if views_str.endswith("K views"):
        return int(float(views_str[:-7]) * 1000)
    elif views_str.endswith("K watching"):
        return int(float(views_str[:-10]) * 1000)
    elif views_str.endswith("M views"):
        return int(float(views_str[:-7]) * 1000000)
    else:
        return int(views_str)


def parse_publish_date(publish_date_str) -> float:
    if publish_date_str.endswith("years ago"):
        return float(float(publish_date_str[:-9]) * 365)
    elif publish_date_str.endswith("year ago"):
        return float(float(publish_date_str[:-8]) * 365)
    elif publish_date_str.endswith("months ago"):
        return float(float(publish_date_str[:-10]) * 30)
    elif publish_date_str.endswith("month ago"):
        return float(float(publish_date_str[:-9]) * 30)
    elif publish_date_str.endswith("weeks ago"):
        return float(float(publish_date_str[:-9]) * 7)
    elif publish_date_str.endswith("days ago"):
        return float(publish_date_str[:-8])
    elif publish_date_str.endswith("day ago"):
        return float(publish_date_str[:-7])
    elif publish_date_str.endswith("hours ago"):
        return float(float(publish_date_str[:-9]) / 24)
    elif publish_date_str.endswith("hour ago"):
        return float(float(publish_date_str[:-8]) / 24)