"""Manual browser smoke check against an isolated database; never contacts TikTok."""
import sys
import tempfile
import threading
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import dashboard_server as server
from src.store import connect, init_db
from src.publisher.http import PublisherAPI
from src.publisher.service import PublisherService
from playwright.sync_api import sync_playwright, expect


def main():
    qa = ROOT / 'data/publisher/qa'
    qa.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=qa) as folder:
        root = Path(folder)
        server.DB_PATH = root / 'test.db'
        with closing(connect(server.DB_PATH)) as conn:
            init_db(conn)
        server.PUBLISHER = PublisherAPI(root, server.DB_PATH)
        server.PUBLISHER.initialize()
        class RemoteFixture:
            def list_files(self):
                return [{'id':f'fixture-{i}','name':f'FINAL_{i}.mp4','mimeType':'video/mp4','size':100+i,'parents':['fixture-folder'],'folder_path':'/Produto QA'} for i in range(20)]
        with closing(connect(server.DB_PATH)) as conn:
            PublisherService(conn,root).index_media(RemoteFixture())
        http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel='chrome', headless=True)
                page = browser.new_page(viewport={'width':1440,'height':1000})
                errors=[]
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{http.server_port}/publisher.html')
                page.locator('nav [data-view=accounts]').click()
                page.locator('#accountForm [name=name]').fill('Ceci QA')
                page.locator('#accountForm button[type=submit]').click()
                expect(page.locator('#accountList')).to_contain_text('Ceci QA')
                page.locator('nav [data-view=products]').click()
                page.locator('#productForm [name=name]').fill('Produto QA')
                page.locator('#productForm [name=short_name]').fill('Produto curto')
                page.locator('#productForm [name=default_caption]').fill('Legenda QA')
                page.locator('#productForm button[type=submit]').click()
                expect(page.locator('#productList')).to_contain_text('Produto QA')
                page.locator('nav [data-view=batch]').click()
                page.locator('#batchForm [name=product_search]').fill('Produto QA [1]')
                page.locator('#batchForm [name=product_search]').dispatch_event('change')
                expect(page.locator('#batchCaptionPreview')).to_have_text('Legenda QA')
                expect(page.locator('#batchForm textarea[name=caption]')).to_have_count(0)
                page.locator('#batchForm [name=account_id]').select_option('1')
                page.locator('#selectAllMedia').check()
                expect(page.locator('#fileCount')).to_contain_text('20 selecionados')
                page.screenshot(path=str(qa/'remote-inbox.png'),full_page=True)
                page.locator('#batchForm button[type=submit]').click()
                expect(page.locator('#draftRows tr')).to_have_count(20)
                page.locator('#bulkEdit').click()
                page.locator('#editForm [name=change_caption]').check()
                page.locator('#editForm [name=caption]').fill('Legenda revisada')
                page.locator('#editForm button.primary').click()
                expect(page.locator('#draftRows .caption').first).to_have_text('Legenda revisada')
                page.screenshot(path=str(qa/'desktop.png'),full_page=True)
                for width in [390,768]:
                    page.set_viewport_size({'width':width,'height':844})
                    page.screenshot(path=str(qa/f'mobile-{width}.png'),full_page=True)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Page overflow'
                page.locator('#draftRows [data-review]').first.click()
                expect(page.locator('#reviewDialog')).to_be_visible()
                expect(page.locator('#reviewBody')).to_contain_text('Legenda revisada')
                expect(page.locator('#reviewBody')).to_contain_text('Produto TikTok Shop ainda precisa ser vinculado manualmente')
                assert not errors, errors
                browser.close()
                assert not (root/'data/publisher/uploads').exists()
                print('PASS: 20 remote drafts, no downloads, bulk edit, review, desktop/mobile, no JS errors')
        finally:
            http.shutdown()
            http.server_close()


if __name__ == '__main__':
    main()
