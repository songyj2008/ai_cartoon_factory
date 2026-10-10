from services import brand
from ui.styles import CSS


def test_official_accounts_are_compact_and_natively_collapsible(monkeypatch):
    monkeypatch.setattr(
        brand,
        "load_brand_info",
        lambda: {
            "signature_valid": True,
            "brand": "Test Brand",
            "douyin": "douyin-id",
            "bilibili_name": "Bilibili Name",
            "bilibili_username": "bilibili-id",
            "runninghub_id": "runninghub-id",
            "runninghub_invite_url": "https://example.test/invite",
        },
    )
    monkeypatch.setattr(brand, "_embedded_douyin_qr_url", lambda: "data:image/png;base64,test")

    html = brand.render_brand_card()

    assert '<details class="official-platform-disclosure" open>' in html
    assert "<summary>" in html
    assert "官方账号与创作福利" in html
    assert 'class="official-platform-chevron" aria-hidden="true"' in html
    assert html.count('class="account-card"') == 3
    assert html.count("<details") == html.count("</details>") == 1

    assert "#official_platform_section" in CSS
    assert ".official-platform-disclosure > summary:focus-visible" in CSS
    assert "margin: 8px 0 0 !important" in CSS
