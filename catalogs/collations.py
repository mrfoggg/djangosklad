import unicodedata


UKRAINIAN_ALPHABET = "абвгґдеєжзиіїйклмнопрстуфхцчшщьюя"
LETTER_ORDER = {letter: index for index, letter in enumerate(UKRAINIAN_ALPHABET)}


def ukrainian_sort_key(value):
    text = unicodedata.normalize("NFC", value).casefold()
    return tuple(LETTER_ORDER.get(char, 100 + ord(char)) for char in text)


def ukrainian_collation(left, right):
    left_key, right_key = ukrainian_sort_key(left), ukrainian_sort_key(right)
    return (left_key > right_key) - (left_key < right_key)


def register_collations(sender, connection, **kwargs):
    if connection.vendor == "sqlite":
        connection.connection.create_collation("ukrainian", ukrainian_collation)
