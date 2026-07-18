from __future__ import annotations

# VK ограничивает текст записи на стене ~16000 символами.
VK_MESSAGE_LIMIT = 16000


def build_post_text(title: str, description: str, ad_block: str) -> str:
    """Собирает текст поста: заголовок, описание, рекламный блок.

    Порядок по ТЗ: оригинальное название -> оригинальное описание -> реклама.
    """
    parts = [p for p in (title.strip(), description.strip(), ad_block.strip()) if p]
    text = "\n\n".join(parts)
    if len(text) <= VK_MESSAGE_LIMIT:
        return text

    # Ужимаем описание, сохраняя заголовок и рекламу целиком.
    reserved = len(title.strip()) + len(ad_block.strip()) + len("\n\n") * 2
    room = VK_MESSAGE_LIMIT - reserved
    trimmed = description.strip()[: max(0, room)].rstrip()
    parts = [p for p in (title.strip(), trimmed, ad_block.strip()) if p]
    return "\n\n".join(parts)
