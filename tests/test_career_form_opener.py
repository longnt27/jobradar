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


def test_form_structure_filters_hidden_dummy_inputs() -> None:
    from job_radar.apply import _form_structure

    async def run():
        html = """
        <main>
          <form action="/apply" method="post" enctype="multipart/form-data">
            <label>First Name <input id="first_name" name="first_name" type="text" required></label>
            <input required="" tabindex="-1" aria-hidden="true" class="remix-css-1a0ro4n-requiredInput" style="opacity: 0" value="">
            <div style="display: none;">
              <input id="iti-search" type="search" placeholder="Search country">
            </div>
            <input type="text" style="display: none">
            <label>Resume <input id="resume" name="resume" type="file" required></label>
            <button type="submit">Submit application</button>
          </form>
        </main>
        """
        playwright, browser, page = await _page(html)
        try:
            form = await _form_structure(page)
            labels = [f["label"] for f in form["fields"]]
            ids = [f["id"] for f in form["fields"]]
            assert ids == ["first_name", "resume"]
            assert labels == ["First Name", "Resume"]
            assert form["fields"][0]["index"] == 0
            # resume is index 4 in form.locator("input,select,textarea")
            assert form["fields"][1]["index"] == 4
        finally:
            await browser.close()
            await playwright.stop()

    asyncio.run(run())

