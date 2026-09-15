from unittest.mock import patch

from app.web.views.landing import render_landing_page


def test_render_landing_page_uses_project_assets():
    with patch.dict("os.environ", {"PHONE_NUMBER": "621234567890"}):
        html = render_landing_page()

    assert "Data dari WhatsApp" in html
    assert 'href="/static/index.css"' in html
    assert 'src="/static/icon.png"' in html
    assert "@tailwindcss/browser" not in html
    assert html.count("https://wa.me/621234567890?text=%2Fhelp") == 4
    assert "6287775535532" not in html
