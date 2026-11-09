from datetime import datetime
from zoneinfo import ZoneInfo

MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def human_date(timestamp: int, tz: str = "Europe/Moscow") -> str:
    moment = datetime.fromtimestamp(int(timestamp), tz=ZoneInfo(tz))
    return f"{moment.day} {MONTHS[moment.month - 1]} в {moment:%H:%M}"


def plural(count: int, one: str, few: str, many: str) -> str:
    """plural(5, 'пост', 'поста', 'постов') -> '5 постов'"""
    tail = abs(count) % 100
    if 11 <= tail <= 14:
        word = many
    elif tail % 10 == 1:
        word = one
    elif 2 <= tail % 10 <= 4:
        word = few
    else:
        word = many
    return f"{count} {word}"
