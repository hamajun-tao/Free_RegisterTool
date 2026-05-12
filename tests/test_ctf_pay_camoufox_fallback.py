import importlib.util
from contextlib import contextmanager
from pathlib import Path


CARD_PY_PATH = Path(__file__).resolve().parents[1] / "CTF-pay" / "card.py"


def _load_card_module():
    spec = importlib.util.spec_from_file_location("ctf_pay_card_for_tests", CARD_PY_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_camoufox_geoip_fallback_skips_geoip_launch_when_extra_missing(monkeypatch):
    card = _load_card_module()
    calls = []

    monkeypatch.setattr(card, "_camoufox_geoip_extra_available", lambda: False, raising=False)

    @contextmanager
    def fake_camoufox(**kwargs):
        calls.append(dict(kwargs))
        yield object()

    with card._camoufox_geoip_fallback(fake_camoufox, geoip=True, headless=True):
        pass

    assert calls == [{"geoip": False, "headless": True}]
