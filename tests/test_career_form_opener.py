import asyncio

from playwright.async_api import async_playwright


async def _page(html: str):
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.set_content(html)
    return playwright, browser, page


def test_career_form_opens_behind_non_apply_button() -> None:
    from job_radar.apply import _open_application_form

    async def run():
        playwright, browser, page = await _page('''
            <main><h1>Software Engineer</h1><button>Read job details</button>
            <button id="join">Join our team</button><section id="application" hidden>
            <form action="/submit" method="post" enctype="multipart/form-data">
            <label>Resume <input name="resume" type="file" required></label>
            <button type="submit">Send</button></form></section></main>
            <script>document.querySelector('#join').onclick = () => {
              document.querySelector('#application').hidden = false;
            };</script>''')
        try:
            opened, form = await _open_application_form(page)
            assert opened is page
            assert form["opener"]["label"] == "Join our team"
            assert form["fields"][0]["name"] == "resume"
            await page.reload()
            await page.set_content('''<main><h1>Software Engineer</h1><button>Read job details</button>
            <button id="join">Join our team</button><section id="application" hidden><form action="/submit" method="post" enctype="multipart/form-data">
            <label>Resume <input name="resume" type="file" required></label><button type="submit">Send</button></form></section></main>
            <script>document.querySelector('#join').onclick=()=>document.querySelector('#application').hidden=false;</script>''')
            _, reopened = await _open_application_form(page, form["opener"])
            assert reopened["signature"] == form["signature"]
        finally:
            await browser.close()
            await playwright.stop()

    asyncio.run(run())


def test_ambiguous_career_buttons_require_choice() -> None:
    from job_radar.apply import _open_application_form

    async def run():
        playwright, browser, page = await _page('''<main><h1>Designer</h1>
          <button>Join our team</button><button>Submit CV</button></main>''')
        try:
            try:
                await _open_application_form(page)
            except ValueError as error:
                assert "multiple" in str(error).lower()
                assert "Join our team" in str(error)
                assert "Submit CV" in str(error)
            else:
                raise AssertionError("Ambiguous actions must not be clicked")
        finally:
            await browser.close()
            await playwright.stop()

    asyncio.run(run())
