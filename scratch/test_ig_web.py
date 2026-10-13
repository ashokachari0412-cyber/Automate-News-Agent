import json
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        cookies = json.load(open('userdata/instagram_cookies.json'))
        pw_cookies = []
        for c in cookies:
            cookie = {'name': c['name'], 'value': c['value'], 'domain': c['domain'], 'path': c.get('path', '/')}
            if 'expirationDate' in c:
                cookie['expires'] = c['expirationDate']
            if 'httpOnly' in c:
                cookie['httpOnly'] = c['httpOnly']
            if 'secure' in c:
                cookie['secure'] = c['secure']
            pw_cookies.append(cookie)
        await ctx.add_cookies(pw_cookies)
        page = await ctx.new_page()
        await page.goto('https://www.instagram.com/', wait_until='networkidle')
        
        # Check text or labels
        html_content = await page.content()
        print("Page title:", await page.title())
        print("Has Create text:", "Create" in html_content)
        print("Has Post text:", "Post" in html_content)
        
        # Check create locator
        create_el = page.locator('span:text-is("Create"), svg[aria-label="New post"]')
        count = await create_el.count()
        print("Create elements found:", count)
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
