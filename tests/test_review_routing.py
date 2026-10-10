"""Review-to-public-reply regressions, with manually labelled customer examples."""
import pytest
import auto_reply_reviews as app


def decision(text, queue='neutral', product='Biscuits'):
    return app.select_reply_decision(app.REVIEW_QUEUES[queue],
        {'review_text': text, 'product': product}, app.build_parser().parse_args([]))


@pytest.mark.parametrize('text,category,topic', [
    ('Received but have yet to try', 'Not Yet Tried', 'try'),
    ('Product quality: yet to try', 'Not Yet Tried', 'try'),
    ('Fast Delivery, Nice packing yet to try', 'Not Yet Tried', 'try'),
    ('Received 6oct2026 but due date of e biscuits is too near 1dec2026', 'Expiry / Date Concern', 'date'),
    ('Good packaging but expiry date is too near', 'Expiry / Date Concern', 'date'),
    ('Fast delivery but the crab roe flavor taste not nice', 'Taste / Product Quality', 'taste'),
    ('Chocolate flavour but taste citrusy 🤨', 'Taste / Product Quality', 'taste'),
    ('Biscuits all crack', 'Damaged / Packaging', 'condition'),
    ('Its lemau the moment i eat it. All 3 boxes', 'Taste / Product Quality', 'quality'),
    ("i haven’t received my items", 'Delivery / Fulfilment', 'delivery'),
    ('Received wrong flavour', 'Missing / Wrong Item', 'items'),
    ('Taste good but too expensive already', 'Price / Value', 'value'),
    ('No damage but biscuits taste stale', 'Taste / Product Quality', 'quality'),
    ('self heating tab is super hard to pull, but soup is okay', 'Product Usability / Instructions', 'instructions'),
    ('did not heat up even after pulling on rope very hard', 'Product Usability / Instructions', 'instructions'),
])
def test_actual_review_routes_to_relevant_reply_and_sheet(text, category, topic):
    result = decision(text)
    assert result.category == category
    assert topic in result.reply_text.lower()
    assert result.auto_submit
    entry = app.ReplyLogEntry('2026-10-10', 'welive_main', 'neutral', '3', 'test', '',
                             'Biscuits', text, result.reply_text, 'submitted', 'test-key')
    assert app.build_google_sheet_row(entry, '2026-10-09')[4] == category


@pytest.mark.parametrize('text', [
    'Received but have yet to try', 'Nice', 'Super fast delivery. Well packed. Taste soo good!',
    'Good packaging, no damage, nothing missing. Taste good.',
    'Delicious! Love the strawberry and matcha flavours!',
    'Easy and convenient to prepare. Like the taste, not too salty. Will buy again.',
    'Great value. Not expensive.', 'Good taste and good buy too.',
])
def test_noncomplaints_do_not_get_apology_or_invented_problem(text):
    result = decision(text)
    assert result.category in {'Positive Feedback', 'Not Yet Tried'}
    assert 'sorry' not in result.reply_text.lower()
    assert 'apologi' not in result.reply_text.lower()
    assert 'did not meet' not in result.reply_text.lower()


def test_mixed_complaints_both_addressed_without_apologising_for_fast_delivery():
    result = decision('Fast delivery but box damaged and wrong flavour received')
    assert 'Missing / Wrong Item' in result.category
    assert 'Damaged / Packaging' in result.category
    assert 'items' in result.reply_text.lower()
    assert 'condition' in result.reply_text.lower()
    assert 'delivery experience' not in result.reply_text.lower()


@pytest.mark.parametrize('text', ['hospitality was great', 'No bugs or mould, tastes good', 'Glass jar is well packed'])
def test_incidental_words_do_not_trigger_serious_hold(text):
    assert decision(text).auto_submit


@pytest.mark.parametrize('text', ['Food poisoning after eating this', 'Found glass in the biscuits',
    'I cut myself with the sharp aluminium', 'Not halal',
    'Good delivery. ' * 30 + 'Found mould inside the packet'])
def test_serious_concerns_are_recognised_even_late_in_review(text, monkeypatch):
    monkeypatch.delenv('AUTO_SUBMIT_HIGH_RISK', raising=False)
    result = decision(text)
    assert result.category == 'Serious Concern / Escalation'
    assert not result.auto_submit
    assert app.google_review_category(app.ReplyLogEntry('2026-10-10','welive_main','negative','1,2',
        'test','','Biscuits',text,'','needs_manual_review_high_risk','key')) == result.category


def test_unknown_review_does_not_invent_a_specific_issue():
    result = decision('🤔')
    assert result.auto_submit
    assert 'sorry' not in result.reply_text.lower()
    assert 'chat' in result.reply_text.lower()


def test_blank_review_and_sheet_share_no_text_category():
    assert decision('').category == app.classify_review_category('') == 'Other / No Review Text'


def test_reply_metadata_not_treated_as_customer_complaint():
    result = decision('|Updated 2 hours ago Received. 1 response(s) 10 October 2026 We are sorry about the damaged packaging')
    assert result.category == 'General Feedback'
    assert 'damage' not in result.reply_text.lower()


def test_future_review_date_is_not_eligible():
    from datetime import date
    assert not app.is_recent_review('2026-10-11', today=date(2026,10,10), recent_days=30)


@pytest.mark.parametrize('text,category', [
    ('Doesnt taste good at all and the price doesnt justify the taste.', 'Taste / Product Quality'),
    ('The chocolate chip cookies aren’t that tasty. It is overhyped', 'Taste / Product Quality'),
    ('Taste not bad just that the cracker is not those super crispy type. Still nice overall', 'Taste / Product Quality'),
    ('The chilli crab taste is missing', 'Taste / Product Quality'),
    ('Product quality: yet to try ordered spicy chicken and sesame, but received sweet n spicy instead of sesame', 'Missing / Wrong Item'),
    ("Product quality: Nice Tried Blueberries but not my type of favor, however, hasn't try the rest", 'Taste / Product Quality'),
    ('Product quality: First tub the heating element already not working', 'Product Usability / Instructions'),
    ('Product quality: I order six item by Only received 5 items', 'Missing / Wrong Item'),
    ('Requested for a mixture of flavours but all four were truffle mushroom.', 'Missing / Wrong Item'),
    ('My package has arrived safely thank you it’s shipped so fast and delivered on time', 'Positive Feedback'),
    ('商品质量: 汤味道还行，只是料很少，不建议购买', 'Price / Value'),
    ('Not really crunchy, not fantastic', 'Taste / Product Quality'),
    ('Product quality: Good', 'Positive Feedback'),
    ('Good long expiry date, well packed', 'Positive Feedback'),
    ('The heating tab worked well. Taste good.', 'Positive Feedback'),
    ('Not damaged, taste good', 'Positive Feedback'),
    ('Good quality. Not stale.', 'Positive Feedback'),
    ('Biscuits are salty and not crispy', 'Taste / Product Quality'),
])
def test_additional_historical_review_regressions(text, category):
    assert decision(text).category == category


def test_long_review_extraction_preserves_late_concern():
    text = 'Good delivery. ' * 30 + 'Found mould inside the packet'
    summary = app.extract_review_summary('3 October 2026 ' + text + ' 0 response(s) Order ID:123 Product ID:456 Biscuits @test Reply Review details')
    assert decision(summary['review_text']).category == 'Serious Concern / Escalation'


def test_existing_seller_response_is_never_replied_to_again():
    from queue_processor import process_queue, ItemDecision, ItemAction
    from review_detection import ReviewCard, PageScan, PageState
    card = ReviewCard('key','order','user','product','Biscuits','bad taste','2026-10-09',
                      2,1,True,'')
    def must_not_submit(*args):
        pytest.fail('A review with an existing seller response must not be submitted again')
    result = process_queue(lambda: PageScan(PageState.LOADED_WITH_ROWS,(card,)),
        lambda card: ItemDecision(ItemAction.REPLY,'test'),must_not_submit)
    assert result.submitted == 0


def test_unknown_response_count_requires_inspection():
    from queue_processor import process_queue, ItemDecision, ItemAction
    from review_detection import ReviewCard, PageScan, PageState
    card = ReviewCard('key','order','user','product','Biscuits','bad taste','2026-10-09',
                      2,None,True,'')
    def must_not_submit(*args):
        pytest.fail('A review must be confirmed unreplied before submission')
    result = process_queue(lambda: PageScan(PageState.LOADED_WITH_ROWS,(card,)),
        lambda card: ItemDecision(ItemAction.REPLY,'test'),must_not_submit)
    assert result.held_manual == 1


@pytest.mark.parametrize('text,category', [
    ('Taste good and a bit too salty for me', 'Taste / Product Quality'),
    ('Tastes good but withno ingredients at all', 'Price / Value'),
])
def test_praise_does_not_hide_concern_in_same_sentence(text, category):
    assert decision(text).category == category
