import pytest

from gateway.asof import AsOfError, check_promql, clip_end, effective_as_of, parse_time
from gateway.redact import redact


@pytest.mark.parametrize("query", [
    'W_value{entity_id="x",domain="sensor"}',
    "avg_over_time(foo[5m] offset 1h)",
    "foo offset 1h30m",
    "foo OFFSET 5m",
    'foo{label="a@b"}',
    'foo{label="offset -1h"}',
    "foo # trailing @ comment",
    'foo{offset="x"}',
    "sum(rate(foo[5m])) / sum(rate(bar[5m]))",
    "foo[1h:5m]",
])
def test_promql_accepted(query):
    check_promql(query)


@pytest.mark.parametrize("query", [
    "foo @ 1700000000",
    "foo @ end()",
    "foo@start()",
    "foo offset -1h",
    "foo OFFSET   -5m",
    "foo offset (1h)",
    "foo offset (0-1h)",
    "foo offset 1h-2h",
    "foo[ -5m ]",
    "sum(foo offset -1m) by (a)",
    "x" * 9000,
])
def test_promql_rejected(query):
    with pytest.raises(AsOfError):
        check_promql(query)


def test_parse_time_forms():
    assert parse_time("1700000000") == 1700000000.0
    assert parse_time("2023-11-14T22:13:20Z") == 1700000000.0
    assert parse_time("2023-11-14T22:13:20") == 1700000000.0  # naive means UTC
    for bad in ("now-1h", "yesterday", "nan", "inf"):
        with pytest.raises(AsOfError):
            parse_time(bad)


def test_effective_as_of_only_tightens():
    assert effective_as_of(None, None) is None
    assert effective_as_of("200", None) == 200
    assert effective_as_of(None, 100.0) == 100
    assert effective_as_of("200", 100.0) == 100  # caller cannot loosen the forced value
    assert effective_as_of("50", 100.0) == 50


def test_clip_end():
    assert clip_end(None, None, now=500) == 500
    assert clip_end(None, 300, now=500) == 300
    assert clip_end(400, 300, now=500) == 300
    assert clip_end(200, 300, now=500) == 200


def test_redact_keys_and_token_urls():
    out = redact([{"entity_picture": "/api/camera_proxy/camera.a?token=abc123&x=1",
                   "attributes": {"access_token": "zzz", "friendly_name": "Cam"}}])
    assert "abc123" not in str(out) and "zzz" not in str(out)
    assert out[0]["attributes"]["friendly_name"] == "Cam"
    assert "x=1" in out[0]["entity_picture"]
