from backgammon_engine_kit.cache import FileCache, MemoryCache, cache_key

from helpers import checker_result, configuration, request


def test_deterministic_cache_key():
    assert cache_key(request()) == cache_key(request())


def test_cache_key_distinguishes_required_identity_inputs():
    base = request()
    different_setting = request(setting="2ply")
    versioned = request()
    versioned = versioned.__class__(
        position=versioned.position,
        engine=versioned.engine,
        analysis_setting=versioned.analysis_setting,
        decision_type=versioned.decision_type,
        dice=versioned.dice,
        configuration=configuration(engine_version="verified-version"),
    )
    assert cache_key(base) != cache_key(different_setting)
    assert cache_key(base) != cache_key(versioned)


def test_cache_hit():
    cache = MemoryCache()
    req = request()
    result = checker_result(req)
    cache.store(req, result)
    lookup = cache.lookup(req)
    assert lookup.outcome == "hit"
    assert lookup.result == result


def test_explicit_cache_miss_has_null_result():
    lookup = MemoryCache().lookup(request())
    assert lookup.outcome == "miss"
    assert lookup.result is None
    assert lookup.to_dict()["result"] is None


def test_file_cache_round_trip_is_a_validated_hit(tmp_path):
    cache = FileCache(tmp_path)
    req = request()
    result = checker_result(req)
    cache.store(req, result)
    lookup = cache.lookup(req)
    assert lookup.outcome == "hit"
    assert lookup.result == result


def test_report_mode_only_changes_key_when_it_changes_data():
    quick = request(report_mode="quick")
    full = request(report_mode="full")
    assert cache_key(quick) == cache_key(full)
    full_changes_data = full.__class__(
        position=full.position,
        engine=full.engine,
        analysis_setting=full.analysis_setting,
        decision_type=full.decision_type,
        dice=full.dice,
        configuration=full.configuration,
        report_mode="full",
        report_mode_changes_data=True,
    )
    assert cache_key(quick) != cache_key(full_changes_data)
