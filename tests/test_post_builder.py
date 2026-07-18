from app.post_builder import VK_MESSAGE_LIMIT, build_post_text


def test_builds_title_description_ad_in_order():
    text = build_post_text("Заголовок", "Описание", "Реклама")
    assert text == "Заголовок\n\nОписание\n\nРеклама"


def test_skips_empty_parts():
    text = build_post_text("Заголовок", "", "Реклама")
    assert text == "Заголовок\n\nРеклама"


def test_trims_when_over_limit_but_keeps_title_and_ad():
    long_description = "x" * (VK_MESSAGE_LIMIT + 500)
    text = build_post_text("Название", long_description, "Реклама")

    assert len(text) <= VK_MESSAGE_LIMIT
    assert text.startswith("Название")
    assert text.endswith("Реклама")
