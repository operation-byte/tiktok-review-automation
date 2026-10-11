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
