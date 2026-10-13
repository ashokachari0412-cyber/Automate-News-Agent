import json
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

async def main():
    image_paths = [
        Path("artifacts/carousels/news_2/slide_01.png").resolve(),
        Path("artifacts/carousels/news_2/slide_02.png").resolve(),
        Path("artifacts/carousels/news_2/slide_03.png").resolve(),
        Path("artifacts/carousels/news_2/slide_04.png").resolve(),
    ]
    for p in image_paths:
        assert p.exists(), f"File {p} does not exist"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 800})
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
        
        # 1. Click Create button
        print("Clicking Create button...")
        create_btn = page.locator('span:text-is("Create"), svg[aria-label="New post"]').first
        await create_btn.click()
        await page.wait_for_timeout(2000)
        
        # Check if submenu appears ("Post")
        post_sub = page.locator('span:text-is("Post")')
        if await post_sub.count() > 0:
            print("Clicking Post submenu...")
            await post_sub.first.click()
            await page.wait_for_timeout(1000)
            
        # 2. File input
        file_input = page.locator('input[type="file"]').first
        print("Setting input files...")
        await file_input.set_input_files([str(p) for p in image_paths])
        await page.wait_for_timeout(3000)
        
        # 3. Check for Next button
        next_btn = page.locator('div[role="button"]:has-text("Next"), button:has-text("Next")')
        print("Next buttons found:", await next_btn.count())
        
        await browser.close()
        print("Test completed successfully!")

if __name__ == "__main__":
    asyncio.run(main())
