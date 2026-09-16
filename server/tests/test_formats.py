from app.services.formats import compile_selector, describe


def profile(**overrides) -> dict:
    base = {
        "name": "test",
        "container": "mkv",
        "max_height": 2160,
        "prefer_codecs": "vp9,av01,h264",
        "prefer_fps": 60,
        "format_override": None,
    }
    base.update(overrides)
    return base


def test_override_wins_outright():
    compiled = compile_selector(profile(format_override="bv*+ba/b"))
    assert compiled["format"] == "bv*+ba/b"
    assert compiled["source"] == "override"
    # Structured fields must not leak in when an override is set.
    assert compiled["format_sort"] == []


def test_blank_override_is_ignored():
    assert compile_selector(profile(format_override="   "))["source"] == "compiled"


def test_max_height_is_a_hard_filter_not_just_a_sort():
    # `-S res:2160` prefers the closest match and will exceed the target when
    # nothing smaller exists, so the cap has to live in the format filter.
    compiled = compile_selector(profile(max_height=1080))
    assert "height<=1080" in compiled["format"]


def test_no_max_height_downloads_the_best_available():
    compiled = compile_selector(profile(max_height=None))
    assert compiled["format"] == "bv*+ba/b"
    assert "height<=" not in compiled["format"]


def test_format_always_has_a_final_fallback():
    # Without a trailing bare "b" a video with no matching format fails outright.
    assert compile_selector(profile())["format"].endswith("/b")


def test_preferred_codec_is_the_first_listed():
    compiled = compile_selector(profile(prefer_codecs="vp9,av01"))
    assert "vcodec:vp9" in compiled["format_sort"]
    assert "vcodec:av01" not in compiled["format_sort"]


def test_codec_order_is_respected():
    compiled = compile_selector(profile(prefer_codecs="av01,vp9"))
    assert "vcodec:av01" in compiled["format_sort"]


def test_fps_preference_is_optional():
    assert "fps" in compile_selector(profile(prefer_fps=60))["format_sort"]
    assert "fps" not in compile_selector(profile(prefer_fps=None))["format_sort"]


def test_resolution_is_always_sorted_first():
    assert compile_selector(profile())["format_sort"][0] == "res"


def test_container_flows_through():
    assert compile_selector(profile(container="mp4"))["merge_output_format"] == "mp4"
    assert compile_selector(profile(container=None))["merge_output_format"] == "mkv"


def test_missing_fields_do_not_crash():
    compiled = compile_selector({})
    assert compiled["format"] == "bv*+ba/b"
    assert compiled["merge_output_format"] == "mkv"


def test_describe_is_ascii_only():
    """describe() is written to the log; a non-ASCII arrow raises
    UnicodeEncodeError on a cp1252 console and would kill the download."""
    for text in (describe(profile()), describe(profile(format_override="b"))):
        text.encode("ascii")


def test_describe_mentions_the_sort_for_compiled_profiles():
    assert "-S" in describe(profile())
    assert "-S" not in describe(profile(format_override="bv*+ba/b"))
