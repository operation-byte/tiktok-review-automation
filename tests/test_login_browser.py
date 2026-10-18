"""Both login services must release the private desktop on every exit path."""
from types import SimpleNamespace

import pytest

from scripts import login_browser


@pytest.mark.parametrize('startup_ready', [True, False])
def test_private_desktop_cleans_up_on_startup_or_browser_failure(monkeypatch, startup_ready):
    processes, stopped = [], []
    def launch(*args, **kwargs):
        process = SimpleNamespace(terminate=lambda: stopped.append('terminate'), wait=lambda **kw: None)
        processes.append(process)
        return process
    monkeypatch.setattr(login_browser.subprocess, 'Popen', launch)
    monkeypatch.setattr(login_browser, 'wait_until', lambda *a: startup_ready)
    monkeypatch.setattr(login_browser.threading, 'Thread', lambda **kw: SimpleNamespace(start=lambda: None))
    with pytest.raises(RuntimeError):
        with login_browser.private_desktop():
            raise RuntimeError('Browser failed')
    assert len(stopped) == len(processes) == (2 if startup_ready else 1)


def test_login_button_is_encoded_in_telegram_notice(monkeypatch):
    import json
    from urllib.parse import parse_qs
    from scripts import notify_failure
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test')
    monkeypatch.setenv('TELEGRAM_ALERT_CHAT_ID', '42')
    monkeypatch.setenv('TELEGRAM_ALLOWED_IDS', '42')
    requests = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(notify_failure.urllib.request, 'urlopen', lambda req, **kw: requests.append(req) or Response())
    markup = {'inline_keyboard': [[{'text': 'Login to BigSeller', 'callback_data': 'bs_login'}]]}
    notify_failure.send_notice('Login required', reply_markup=markup)
    body = parse_qs(requests[0].data.decode())
    assert json.loads(body['reply_markup'][0]) == markup
    assert body['chat_id'] == ['42']
