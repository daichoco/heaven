CHUBBY_SHOP_TERMS = (
    "ぽっちゃり",
    "ぽちゃ",
    "ポッチャリ",
    "ポチャ",
    "マシュマロ",
)


def is_chubby_shop(shop, industry_labels=()):
    return any(
        term.casefold() in text.casefold()
        for text in (shop, *industry_labels)
        for term in CHUBBY_SHOP_TERMS
    )
