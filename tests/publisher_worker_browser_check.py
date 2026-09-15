"""Exercise the page object against intercepted fixture HTML; no real TikTok traffic."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright
from src.publisher.worker import StudioPage, PreparationError


HTML = """<!doctype html><html><body>
<input type=file onchange="document.querySelector('#done').hidden=false">
<div id=done hidden>Upload completed</div><textarea id=caption></textarea>
<button id=add onclick="window.productTouched=true">Add product</button><input id=search>
<button class=result data-product-id=wrong onclick="window.chosen='wrong'">Other product</button>
<button class=result data-product-id=123 onclick="window.chosen='123'">Correct product</button>
<button id=confirm onclick="document.querySelector('#selected').setAttribute('data-product-id',window.chosen)">Confirm product</button>
<div id=selected></div><select id=visibility><option>PUBLIC</option><option>PRIVATE</option></select>
<input type=checkbox id=comments><button id=publish onclick="window.published=true">Publish</button>
<button id=save onclick="localStorage.setItem('caption',document.querySelector('#caption').value);localStorage.setItem('product',window.chosen);document.querySelector('#saved').hidden=false">Save draft</button>
<a id=saved hidden data-draft-id=fixture-123 href="https://www.tiktok.com/draft/fixture-123">Saved</a>
</body></html>"""

PERSISTED = """<html><body><div id=persisted data-draft-id=fixture-123></div>
<div id=filename>video.mp4</div><div id=persisted-caption></div><div id=persisted-product></div><div id=processed>Processed</div>
<script>document.querySelector('#persisted-caption').textContent=localStorage.getItem('caption');document.querySelector('#persisted-product').setAttribute('data-product-id',localStorage.getItem('product'));</script></body></html>"""


def main():
    folder = ROOT / 'data/publisher/qa'
    folder.mkdir(parents=True, exist_ok=True)
    mapping = {'validated':True}
    mapping.update({k:{'selector':v} for k,v in {
        'file_input':'input[type=file]','upload_complete':'#done','caption':'#caption',
        'visibility':'#visibility','comments':'#comments'}.items()})
    with tempfile.TemporaryDirectory(dir=folder) as temp:
        path = Path(temp) / 'video.mp4'
        path.write_bytes(b'fixture')
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            context=browser.new_context(offline=True)
            page=context.new_page()
            page.route('**/*',lambda route: route.fulfill(body=HTML,content_type='text/html'))
            studio=StudioPage(page,mapping)
            checks=studio.prepare({'caption':'Exact caption'},
                                  {'visibility':'PRIVATE','comments':True},path,lambda *x:None)
            assert all(checks.values())
            assert not page.evaluate('!!window.chosen || !!window.productTouched')
            assert 'product' not in checks
            assert not page.evaluate('!!window.published')
            assert not page.evaluate('!!window.published')
            mapping.update({'durable_confirmation_validated':True,'saved_draft_id_attribute':'data-draft-id','saved_draft_url_attribute':'href'})
            mapping.update({k:{'selector':v} for k,v in {'save_draft':'#save','saved_draft':'#saved','persisted_draft':'#persisted','persisted_filename':'#filename','persisted_caption':'#persisted-caption','persisted_processed':'#processed'}.items()})
            page.unroute('**/*')
            page.route('**/*',lambda route:route.fulfill(body=PERSISTED if '/draft/' in route.request.url else HTML,content_type='text/html'))
            checks=studio.prepare({'caption':'Exact caption','filename':'video.mp4','media_asset_id':1},
                                  {'visibility':'PUBLIC','comments':True},path,lambda *x:None)
            assert checks['durable'] and checks['tiktok_draft_id']=='fixture-123'
            assert '/draft/fixture-123' in page.url
            assert page.locator('input[type=file]').count()==0
            page.unroute('**/*')
            page.route('**/*',lambda route: route.fulfill(body='<html><body>Verify you are human CAPTCHA</body></html>',content_type='text/html'))
            try:
                studio.prepare({'caption':'Exact caption'},
                               {'visibility':'PUBLIC','comments':False},path,lambda *x:None)
                raise AssertionError('Challenge incorrectly accepted')
            except PreparationError as exc:
                assert exc.code=='MANUAL_ACTION_REQUIRED'
            browser.close()
    print('PASS OFFLINE: caption, upload fixture, persistence, challenge; no product association or publication')


if __name__ == '__main__':
    main()
