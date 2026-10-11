import pytest

from foncier_mcp.errors import FeatureDisabled, InvalidInput, QuotaExceeded, Unauthorized
from foncier_mcp.tiers import KeyStore, QuotaGuard, UsageCounter, tiers_for
from helpers import make_settings


def test_issue_lookup_revoke(settings):
    store = KeyStore(settings.keys_file)
    tiers = tiers_for(settings)
    plain, record = store.issue("Someone@Example.org", "free", tiers)
    assert plain.startswith("fm_") and record.email == "someone@example.org"
    assert plain not in settings.keys_file.read_text(encoding="utf-8")
    found = store.lookup(plain)
    assert found is not None and found.id == record.id and found.active
    assert store.lookup("fm_nope") is None
    assert store.revoke(record.id) is True
    assert store.lookup(plain).active is False
    assert store.revoke(record.id) is False
    assert [k.id for k in store.list()] == [record.id]


def test_issue_refuses_bad_input(settings):
    store = KeyStore(settings.keys_file)
    tiers = tiers_for(settings)
    with pytest.raises(InvalidInput):
        store.issue("not-an-email", "free", tiers)
    with pytest.raises(InvalidInput):
        store.issue("a@b.cd", "gold", tiers)
    with pytest.raises(FeatureDisabled):
        store.issue("a@b.cd", "paid", tiers)


def test_usage_counter_increments(settings):
    counter = UsageCounter(settings.usage_file)
    assert counter.used("h") == 0
    assert counter.hit("h") == 1
    assert counter.hit("h") == 2
    assert counter.used("h") == 2
    assert counter.used("other") == 0


def test_guard_local_and_unauthorized(settings):
    guard = QuotaGuard(settings)
    assert guard.check(None).tier == "local"
    with pytest.raises(Unauthorized):
        guard.check({})
    with pytest.raises(Unauthorized):
        guard.check({"X-API-Key": "fm_unknown"})


def test_guard_quota(settings):
    guard = QuotaGuard(settings)
    plain, _ = guard.keys.issue("a@b.cd", "free", guard.tiers)
    access = None
    for used in range(1, settings.free_daily_quota + 1):
        access = guard.check({"x-api-key": plain})
        assert access.used_today == used and access.tier == "free"
    assert access is not None and access.remaining == 0
    with pytest.raises(QuotaExceeded):
        guard.check({"x-api-key": plain})


def test_guard_without_key_requirement(tmp_path):
    guard = QuotaGuard(make_settings(tmp_path, require_api_key=False))
    assert guard.check({}).tier == "local"


def test_paid_key_downgraded_when_flag_off(tmp_path):
    on = make_settings(tmp_path, paid_tier_enabled=True)
    plain, _ = KeyStore(on.keys_file).issue("a@b.cd", "paid", tiers_for(on))
    guard_on = QuotaGuard(on)
    access = guard_on.check({"x-api-key": plain})
    assert access.tier == "paid" and not access.downgraded
    assert guard_on.feature_allowed(access, "pdf_export")

    off = make_settings(tmp_path, paid_tier_enabled=False)
    guard_off = QuotaGuard(off)
    access = guard_off.check({"x-api-key": plain})
    assert access.tier == "free" and access.downgraded
    assert not guard_off.feature_allowed(access, "pdf_export")
