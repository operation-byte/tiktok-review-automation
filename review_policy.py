"""Deterministic review routing shared by public replies and KPI categories.

Recognise explicit concerns before praise. Unknown wording gets an invitation
for details rather than an invented complaint. This is not a language model.
"""
import re
import unicodedata

SERIOUS = 'Serious Concern / Escalation'
NO_TEXT = 'Other / No Review Text'
NOT_TRIED = 'Not Yet Tried'
POSITIVE = 'Positive Feedback'
GENERAL = 'General Feedback'
UNKNOWN = 'Other / Needs Review'


def normalise(text):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', text or '').lower()
                  .replace('’', "'").replace('‘', "'")).strip()


def matches(pattern, text):
    return re.search(pattern, text) is not None


def concern_text(text):
    # Remove explicit denials only, not arbitrary clauses containing "not".
    # For example, "not too salty" is praise; "not fresh" is a complaint.
    return re.sub(
        r'\b(?:no|without|nothing|not)\s+(?:(?:any|signs? of)\s+)?'
        r'(?:damage(?:d)?|broken|missing|bugs?|mould|mold|insects?|leaks?|stale)'
        r'(?:\s+(?:or|and)\s+(?:damage|missing|bugs?|mould|mold|insects?|leaks?))*\b'
        r"|\bnot\s+(?:too\s+)?(?:expensive|salty|sweet|spicy|sour|bitter|bad)\b",
        '', text)


def is_serious(text):
    text = concern_text(normalise(text))
    return matches(
        r'\b(?:food poison(?:ing)?|made me sick|fell sick|hospital|hospitalised|hospitalized|'
        r'allergic|allergy|anaphyl\w*|mould\w*|mold\w*|insects?|bugs?|foreign object|'
        r'poison\w*|counterfeit|fake|fraud|scam|legal action|police report|hsa|'
        r'case against|made in china|from japan|country of origin|fooled|cheat\w*|'
        r'not halal|non[- ]halal|cut myself|injur\w*|burnt myself|burned myself)\b'
        r'|\b(?:found|contains?|piece\w* of|bits? of)\s+(?:\w+\s+){0,3}(?:glass|metal)\b'
        r'|\b(?:glass|metal)\s+(?:inside|in my|in the food)\b', text)


# Only complaint phrases, not "packaging", "delivery", "quality" by themselves.
ISSUES = (
    ('Missing / Wrong Item',
     r'\b(?:missing|wrong|incorrect)\s+(?:item\w*|product\w*|flavou?r\w*|order|quantity)\b'
     r'|\b(?:missing|short of|short item|less item)\b(?!\s+(?:taste|flavou?r))'
     r'|\b(?:ordered|requested).{0,100}\b(?:instead|but received|but i received|got \d)\b'
     r'|\b(?:without|missing)\s+(?:the\s+)?(?:sauce|free|gift)\b'
     r'|\b(?:order|ordered|requested).{0,120}\b(?:only received|but all)\b'),
    ('Damaged / Packaging',
     r'\b(?:damage\w*|defect\w*|broken|broke|burst|leak\w*|dent(?:ed)?|crush\w*|spill\w*|'
     r'crack(?:s|ed|ing)?|crumbl\w*|crumbs|bits and pieces|torn|arrived open)\b'
     r'|\b(?:poor|bad|inadequate)\s+pack(?:aging|ing)\b'
     r'|\bpack(?:aging|ing)\s+(?:was\s+|is\s+)?(?:so\s+)?(?:poor|bad)\b'),
    ('Expiry / Date Concern',
     r'\b(?:expiry|expired|expire\w*|best before|manufacturing date|mfg|shelf[- ]life|'
     r'date marking|due date|use[- ]by)\b'),
    ('Delivery / Fulfilment',
     r"\b(?:(?:did\s*not|didn't|haven't|have not|never|not)\s+receiv\w*|"
     r'(?:delivery|shipping|shipment|courier)\s+(?:was\s+|is\s+)?(?:too\s+|very\s+)?(?:late|slow|poor|bad)|'
     r'(?:late|slow|poor|bad)\s+(?:delivery|shipping)|delivered late|received late|delay\w*|'
     r'delivery timing|delivery at \d)\b'),
    ('Product Usability / Instructions',
     r"\b(?:(?:hard|difficult) to (?:pull|open)|(?:cannot|can't|cant) (?:pull|open|heat|self heat)|"
     r"(?:doesn't|doesnt|does not|did not|didn't|not|wasn't able to) heat|"
     r'(?:heating|heater|heat pack)(?: element)?\s+(?:(?:is|already)\s+)?not working|still cold|'
     r'how to use|(?:unclear|missing|confusing) instructions?)\b'),
    ('Customer Service',
     r"\b(?:no (?:response|reply)|(?:did not|didn't|not) (?:reply|respond\w*)|rude|"
     r'(?:bad|poor|unhelpful) (?:service|support)|auto reply|'
     r'(?:service|support)\s+(?:is|was)\s+(?:bad|poor)|not responding)\b'),
    ('Price / Value',
     r'\b(?:expensive|not worth|overpriced|price too high|small portion|portion too small|'
     r'(?:filling|portion|ingredients|serving)\s+(?:is\s+|are\s+)?(?:very\s+)?(?:little|small))\b'
     r'|料很少|分量少|太贵'),
    ('Taste / Product Quality',
     r"\b(?:bland|(?:too|bit|very) (?:salty|sweet|sour|bitter|spicy)|not (?:that |very )?nice|stale|rancid|"
     r"not fresh|not to my liking|(?:don't|dont|didn't|didnt) (?:really )?like|"
     r'lemau|laohong|not (?:really |so |that |those super )?(?:crispy|crunchy|cruncy)|soft|weird|watery|taste meh|'
     r"taste so so|taste okok|tastes? citrusy|oil taste|not my type|aren't (?:that )?tasty|"
     r"(?:doesnt|doesn't|does not|did not|didn't) taste|(?:taste|flavou?r) is missing)\b"),
)

NOT_TRIED_PATTERN = (r"\b(?:yet to (?:try|taste)|not (?:yet )?(?:tried|try|tasted)|"
                     r"(?:haven't|havent|have not|hasn't) (?:tried|try|tasted)|review later)\b")
PRAISE_PATTERN = r'\b(?:good|great|nice|delicious|tasty|love|loved|well packed|fast delivery|buy again|like the taste|arrived safely|delivered on time)\b'
NEGATIVE_PATTERN = (r"\b(?:not|don't|dont|didn't|didnt|doesn't|doesnt|does not|aren't|arent|"
                    r"wasn't|wasnt|stale|rancid|lemau|laohong|weird|soft|watery|bitter|"
                    r"disappointed|disappointing|bad|poor|overhyped)\b")


def categories(text):
    review = normalise(text)
    if not review:
        return (NO_TEXT,)
    if is_serious(review):
        return (SERIOUS,)
    concern = concern_text(review)
    # Praise about flavour or a food's flavour name isn't a taste complaint.
    # Split contrast clauses so "taste good BUT stale" still retains the issue.
    clauses = re.split(r'[.!?;,]|\b(?:but|however|although)\b', concern)
    taste_concerns = []
    for clause in clauses:
        if matches(NOT_TRIED_PATTERN, clause):
            continue
        if matches(PRAISE_PATTERN, clause) and not matches(NEGATIVE_PATTERN, clause):
            continue
        taste_concerns.append(clause)
    taste_text = ' '.join(taste_concerns)
    found = []
    for name, pattern in ISSUES:
        candidate = taste_text if name == 'Taste / Product Quality' else concern
        if name == 'Missing / Wrong Item':
            candidate = re.sub(r'\b(?:taste|flavou?r) is missing\b', '', candidate)
        if name == 'Expiry / Date Concern':
            candidate = re.sub(r'\b(?:good |long |ample )+(?:expiry date|shelf life)\b', '', candidate)
        if matches(pattern, candidate):
            found.append(name)
    if found:
        return tuple(found)
    if matches(NOT_TRIED_PATTERN, review):
        return (NOT_TRIED,)
    if matches(PRAISE_PATTERN, concern) and not matches(NEGATIVE_PATTERN, concern):
        return (POSITIVE,)
    # An unrecognised taste description is acknowledged without labelling it bad.
    if matches(r'\b(?:taste\w*|flavou?r\w*)\b', taste_text):
        return ('Taste / Product Quality',)
    if matches(r'^(?:received|received safely|okay|ok|its okay|it\'s okay)[.! ]*$', review):
        return (GENERAL,)
    return (UNKNOWN,)


ACKNOWLEDGEMENTS = {
    'Missing / Wrong Item': 'the missing or incorrect items',
    'Damaged / Packaging': 'the condition of the items or packaging',
    'Expiry / Date Concern': 'the date marking and remaining shelf life',
    'Delivery / Fulfilment': 'the delivery experience',
    'Product Usability / Instructions': 'the difficulty using the product',
    'Customer Service': 'the service you received',
    'Price / Value': 'the value or portion size',
    'Taste / Product Quality': 'the taste or product quality',
}


def compose_reply(labels):
    if labels == (NOT_TRIED,):
        return 'Thank you for your order! We hope you enjoy it when you get a chance to try it.'
    if labels == (POSITIVE,):
        return 'Thank you for sharing your feedback and for your support!'
    if labels[0] in {GENERAL, UNKNOWN}:
        return ('Thank you for sharing your feedback. If there is anything you would like us to '
                'look into, please contact us via TikTok Shop chat with your order details.')
    concerns = [ACKNOWLEDGEMENTS[label] for label in labels]
    included = concerns[:3]
    topics = ', '.join(included[:-1]) + ' and ' + included[-1] if len(included) > 1 else included[0]
    if len(concerns) > 3:
        topics += ' and the other concerns raised'
    reply = f'Thank you for your feedback about {topics}. '
    if 'Taste / Product Quality' not in labels or len(labels) > 1:
        reply += 'We are sorry for the concern. '
    reply += 'Please contact us via TikTok Shop chat with your order details'
    if 'Expiry / Date Concern' in labels:
        reply += ' and photos of the items, packaging and date marking'
    elif any(label in labels for label in ('Damaged / Packaging', 'Missing / Wrong Item')):
        reply += ' and photos of the items and packaging'
    reply += ' so we can check and assist.'
    if labels == ('Product Usability / Instructions',):
        reply += ' Please follow the instructions on the packaging.'
    return reply
