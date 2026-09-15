import os

from app.web.templating import templates


def render_landing_page() -> str:
    return templates.get_template("pages/LandingPage.html").render(
        phone_number=os.environ["PHONE_NUMBER"],
    )
