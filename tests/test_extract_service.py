import copy

import pytest
from PIL import Image

from i2c.errors import ManualReviewRequired
from i2c.extract.cache import ExtractionCache
from i2c.extract.providers import ProviderResult, prepare_image
from i2c.extract.service import extract_order

from conftest import KNOWN_METHODS


class FakeProvider:
    name = "fake"

    def __init__(self, responses, model="fake-1"):
        self.model = model
        self.responses = list(responses)
        self.calls = []          # feedback passed on each call

    def extract(self, image, feedback=None):
        self.calls.append(feedback)
        return ProviderResult(copy.deepcopy(self.responses[len(self.calls) - 1]), 100, 50, 0.01)


@pytest.fixture
def image(tmp_path):
    p = tmp_path / "order.png"
    Image.new("RGB", (60, 40), "white").save(p)
    return p


@pytest.fixture
def cache(tmp_path):
    return ExtractionCache(tmp_path / "cache")


def run(image, provider, cache=None, **kw):
    return extract_order(image, provider, cache=cache, max_attempts=2, known_payment_methods=KNOWN_METHODS, **kw)


def test_success_first_attempt(image, cache, expected_dict):
    p = FakeProvider([expected_dict])
    res = run(image, p, cache)
    assert res.order.to_json_dict()["totals"]["gross"] == "678.30"
    assert res.meta["attempts"] == 1 and not res.meta["cache_hit"]
    assert res.meta["input_tokens"] == 100
    assert p.calls == [None]


def test_cache_hit_skips_provider(image, cache, expected_dict):
    run(image, FakeProvider([expected_dict]), cache)
    p2 = FakeProvider([])
    res = run(image, p2, cache)
    assert res.meta["cache_hit"]
    assert p2.calls == []
    assert res.order.items[0].sku == "CHR-ERG-01"


def test_no_cache_flag_calls_provider(image, cache, expected_dict):
    run(image, FakeProvider([expected_dict]), cache)
    p2 = FakeProvider([expected_dict])
    run(image, p2, cache, use_cache=False)
    assert len(p2.calls) == 1


def test_cache_key_depends_on_model(image, cache, expected_dict):
    run(image, FakeProvider([expected_dict], model="a"), cache)
    p = FakeProvider([expected_dict], model="b")
    assert not run(image, p, cache).meta["cache_hit"]
    assert len(p.calls) == 1


def test_retry_with_feedback_then_success(image, cache, expected_dict, mutate):
    bad = mutate(lambda d: d["items"][0].update(line_net_total="500.00"))
    p = FakeProvider([bad, expected_dict])
    res = run(image, p, cache)
    assert res.meta["attempts"] == 2
    assert res.meta["output_tokens"] == 100
    assert p.calls[0] is None
    assert "line_net_mismatch" in p.calls[1]
    assert "500.00" in p.calls[1]          # previous answer is included


def test_schema_error_is_retried(image, expected_dict, mutate):
    bad = mutate(lambda d: d.update(order_date="14/07/2026"))
    p = FakeProvider([bad, expected_dict])
    assert run(image, p).meta["attempts"] == 2
    assert "schema" in p.calls[1] and "order_date" in p.calls[1]


def test_two_failures_raise_manual_review_and_do_not_cache(image, cache, mutate):
    bad = mutate(lambda d: d["totals"].update(net="999.00"))
    with pytest.raises(ManualReviewRequired) as exc:
        run(image, FakeProvider([bad, bad]), cache)
    assert exc.value.step == "extract"
    assert "net_total_mismatch" in exc.value.reason
    assert list(cache.directory.glob("*.json")) == []


def test_warnings_are_returned_not_raised(image, mutate):
    d = mutate(lambda d: d.update(currency="USD"))
    res = run(image, FakeProvider([d]))
    assert [i.code for i in res.issues] == ["currency"]


def test_invalid_cache_entry_is_ignored(image, cache, expected_dict, mutate):
    run(image, FakeProvider([expected_dict]), cache)
    [entry] = cache.directory.glob("*.json")
    entry.write_text("{not json", encoding="utf-8")
    p = FakeProvider([expected_dict])
    assert not run(image, p, cache).meta["cache_hit"]
    assert len(p.calls) == 1


def test_prepare_image_downscales_and_hashes_original(tmp_path):
    p = tmp_path / "big.png"
    Image.new("RGB", (3200, 1600), "white").save(p)
    img = prepare_image(p, max_long_side=1568)
    assert (img.width, img.height) == (1568, 784)
    assert img.media_type == "image/png"
    import hashlib
    assert img.sha256 == hashlib.sha256(p.read_bytes()).hexdigest()


def test_prepare_image_keeps_small_png_bytes(image):
    img = prepare_image(image)
    assert img.data == image.read_bytes()


def test_sample_image_prepares():
    from pathlib import Path
    sample = Path(__file__).parents[1] / "samples" / "order_01.png"
    img = prepare_image(sample)
    assert max(img.width, img.height) <= 1568
