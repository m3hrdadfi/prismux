import math

# range key -> (window_seconds, bucket_seconds)
RANGE_PRESETS = {
    "5m": (300, 10),
    "30m": (1_800, 60),
    "1h": (3_600, 120),
    "6h": (21_600, 600),
    "24h": (86_400, 1_800),
}
DEFAULT_RANGE = "5m"
MIN_BUCKET_SECONDS = 10
ALL_TARGET_BUCKETS = 40
ALL_FALLBACK_WINDOW_SECONDS = 300  # used when "all" is selected but there's no data yet


def resolve_range(range_key: str, oldest_age_seconds: float | None) -> tuple[int, int, int]:
    """Returns (window_seconds, bucket_seconds, bucket_count) for a range key.

    oldest_age_seconds is `now - oldest request timestamp`, used only for
    "all" — the chart shows the actual history span, not a padded window.
    """
    if range_key in RANGE_PRESETS:
        window_seconds, bucket_seconds = RANGE_PRESETS[range_key]
        return window_seconds, bucket_seconds, window_seconds // bucket_seconds

    if oldest_age_seconds is None or oldest_age_seconds <= 0:
        window_seconds = ALL_FALLBACK_WINDOW_SECONDS
    else:
        window_seconds = max(60, math.ceil(oldest_age_seconds))
    bucket_seconds = max(MIN_BUCKET_SECONDS, round(window_seconds / ALL_TARGET_BUCKETS))
    bucket_count = math.ceil(window_seconds / bucket_seconds)
    return window_seconds, bucket_seconds, bucket_count
