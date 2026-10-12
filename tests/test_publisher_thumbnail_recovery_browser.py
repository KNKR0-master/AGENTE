"""Chrome-only thumbnail DOM regression, with all requests intercepted locally."""
import struct
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
def png_chunk(kind, data):
    return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))


PNG = (b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', struct.pack('!IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
       + png_chunk(b'IDAT', zlib.compress(b'\x00\x00\xff\x00\xff')) + png_chunk(b'IEND', b''))


class ThumbnailRecoveryBrowserTests(unittest.TestCase):
    def test_valid_image_survives_refresh_and_partial_response_without_new_fetch(self):
        from playwright.sync_api import sync_playwright
        script = (ROOT / 'dashboard/publisher.js').read_text(encoding='utf-8')
        controller = script[script.index('const thumbnailImages=new Map();'):script.index('function queueThumbnail(')]
        markup = '<img data-thumbnail-url="/api/publisher/library/thumbnail/file_one"><span hidden>Unavailable</span>'
        requests = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page()
                def route(request):
                    if '/thumbnail/' in request.request.url:
                        requests.append(request.request.url)
                        request.fulfill(status=200 if len(requests) == 1 else 503,
                                        content_type='image/png', body=PNG if len(requests) == 1 else b'')
                    else:
                        request.fulfill(content_type='text/html', body='<div id="fixture"></div>')
                page.route('**/*', route)
                page.goto('http://publisher-fixture.test/')
                page.add_script_tag(content=controller)
                page.evaluate('(markup)=>{fixture.innerHTML=markup;bindThumbnails(fixture);}', markup)
                page.wait_for_function('fixture.querySelector("img").naturalWidth===1')
                page.evaluate('window.original=fixture.querySelector("img")')
                for _ in range(3):
                    page.evaluate('(markup)=>{rememberThumbnails(fixture);fixture.innerHTML=markup;bindThumbnails(fixture);}', markup)
                    self.assertTrue(page.evaluate('fixture.querySelector("img")===original&&!original.hidden&&original.naturalWidth===1'))
                self.assertEqual(len(requests), 1)
                page.evaluate('(markup)=>{rememberThumbnails(fixture);fixture.innerHTML=markup;bindThumbnails(fixture);}',
                              markup.replace('file_one', 'file_two'))
                page.wait_for_function('fixture.querySelector("img").hidden===true')
                self.assertIn('file_two', requests[-1])
                self.assertFalse(page.evaluate('fixture.querySelector("img")===original'))
            finally:
                browser.close()
