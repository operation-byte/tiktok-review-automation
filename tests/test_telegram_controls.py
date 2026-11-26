from scripts.remote_login import verified_login

class Page:
    url='https://seller-sg.tiktok.com/product/rating'
    def __init__(self,text):self.text=text
    def locator(self,*a):return self
    def inner_text(self,**kw):return self.text

def test_login_needs_authenticated_review_page():
    assert verified_login(Page('Product ratings Negative review'))
    p=Page('Product ratings');p.url='https://seller-sg.tiktok.com/login'
    assert not verified_login(p)
    assert not verified_login(Page('Please log in'))


def test_save_finds_new_ratings_tab_and_refreshes_stale_view(monkeypatch):
    from types import SimpleNamespace
    from scripts import remote_login

    clock = [0.0]
    monkeypatch.setattr(remote_login.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(remote_login.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    original = Page('Sign in')
    original.url = 'https://seller-sg.tiktok.com/login'
    ratings = Page('Loading')
    ratings.url += '?from=login'
    refreshed = []
    def reload(**kwargs):
        refreshed.append(True)
        ratings.text = 'Negative reviews'
    ratings.reload = reload
    assert remote_login.wait_for_verified_login(SimpleNamespace(pages=[original, ratings]), timeout=3)
    assert refreshed == [True]
    original.url = 'https://example.com/product/rating'
    original.text = 'Product ratings'
    assert not remote_login.wait_for_verified_login(SimpleNamespace(pages=[original]), timeout=1)
