try:
    from pypinyin import lazy_pinyin
except ImportError:  # pragma: no cover
    lazy_pinyin = None


def artist_sort_key(name: str) -> tuple:
    value = (name or "").strip()
    if lazy_pinyin:
        text = "".join(lazy_pinyin(value, errors="default")).casefold()
    else:
        text = value.casefold()
    return (text, value.casefold())
