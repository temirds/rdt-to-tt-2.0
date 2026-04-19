from __future__ import annotations

import re


def normalize_russian_numbers(text: str) -> str:
    return re.sub(r"\b\d{1,4}\b", lambda match: number_to_russian(int(match.group(0))), text)


def number_to_russian(number: int) -> str:
    if number < 0 or number > 9999:
        return str(number)

    units = {
        0: "ноль",
        1: "один",
        2: "два",
        3: "три",
        4: "четыре",
        5: "пять",
        6: "шесть",
        7: "семь",
        8: "восемь",
        9: "девять",
    }
    teens = {
        10: "десять",
        11: "одиннадцать",
        12: "двенадцать",
        13: "тринадцать",
        14: "четырнадцать",
        15: "пятнадцать",
        16: "шестнадцать",
        17: "семнадцать",
        18: "восемнадцать",
        19: "девятнадцать",
    }
    tens = {
        20: "двадцать",
        30: "тридцать",
        40: "сорок",
        50: "пятьдесят",
        60: "шестьдесят",
        70: "семьдесят",
        80: "восемьдесят",
        90: "девяносто",
    }
    hundreds = {
        100: "сто",
        200: "двести",
        300: "триста",
        400: "четыреста",
        500: "пятьсот",
        600: "шестьсот",
        700: "семьсот",
        800: "восемьсот",
        900: "девятьсот",
    }

    def under_thousand(value: int, feminine: bool = False) -> str:
        parts: list[str] = []
        if value >= 100:
            parts.append(hundreds[value // 100 * 100])
            value %= 100
        if 10 <= value <= 19:
            parts.append(teens[value])
            return " ".join(parts)
        if value >= 20:
            parts.append(tens[value // 10 * 10])
            value %= 10
        if value:
            if feminine and value == 1:
                parts.append("одна")
            elif feminine and value == 2:
                parts.append("две")
            else:
                parts.append(units[value])
        return " ".join(parts) or units[0]

    if number < 1000:
        return under_thousand(number)

    thousands = number // 1000
    rest = number % 1000
    if 11 <= thousands % 100 <= 14:
        thousand_word = "тысяч"
    elif thousands % 10 == 1:
        thousand_word = "тысяча"
    elif thousands % 10 in {2, 3, 4}:
        thousand_word = "тысячи"
    else:
        thousand_word = "тысяч"

    parts = [under_thousand(thousands, feminine=True), thousand_word]
    if rest:
        parts.append(under_thousand(rest))
    return " ".join(parts)
