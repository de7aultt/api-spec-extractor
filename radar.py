import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

FEED_URL = "https://raw.githubusercontent.com/arkadiyt/bounty-targets-data/master/data/hackerone_data.json"
DEFAULT_CACHE_PATH = Path("data/hackerone_targets.json")
CACHE_MAX_AGE_SECONDS = 24 * 60 * 60
REQUEST_TIMEOUT_SECONDS = 30.0
WEB_ASSET_TYPES = frozenset({"URL", "WILDCARD"})


class RadarError(RuntimeError):
    pass


def _cache_is_fresh(cache_path: Path, max_age_seconds: float) -> bool:
    if not cache_path.exists():
        return False
    age_seconds = time.time() - cache_path.stat().st_mtime
    return age_seconds < max_age_seconds


def _load_cache(cache_path: Path) -> list[dict]:
    try:
        raw_text = cache_path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except (OSError, json.JSONDecodeError) as error:
        raise RadarError(f"Failed to read cached targets from {cache_path}: {error}") from error
    if not isinstance(data, list):
        raise RadarError(f"Cached targets file {cache_path} does not contain a JSON list")
    return data


def _download_feed(feed_url: str, timeout: float) -> list[dict]:
    try:
        response = requests.get(feed_url, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as error:
        raise RadarError(f"Failed to download target feed: {error}") from error
    if not isinstance(data, list):
        raise RadarError("Target feed did not return a JSON list")
    return data


def _save_cache(cache_path: Path, data: list[dict]) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data), encoding="utf-8")
    except OSError as error:
        raise RadarError(f"Failed to write target cache to {cache_path}: {error}") from error


def fetch_or_load_targets(
    force_refresh: bool = False,
    cache_path: Path = DEFAULT_CACHE_PATH,
    feed_url: str = FEED_URL,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> list[dict]:
    cache_path = Path(cache_path)
    if not force_refresh and _cache_is_fresh(cache_path, CACHE_MAX_AGE_SECONDS):
        return _load_cache(cache_path)
    try:
        data = _download_feed(feed_url, timeout)
    except RadarError:
        if cache_path.exists():
            return _load_cache(cache_path)
        raise
    _save_cache(cache_path, data)
    return data


def _coerce_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace("$", "").replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0
    return 0.0


def _extract_bounty_range(program: dict) -> tuple[float, float, float]:
    minimum = _coerce_float(
        program.get("bounty_min")
        or program.get("minimum_bounty")
        or program.get("bounty_low")
    )
    maximum = _coerce_float(
        program.get("bounty_max")
        or program.get("maximum_bounty")
        or program.get("bounty_high")
    )
    average = _coerce_float(
        program.get("average_bounty")
        or program.get("average_bounty_lower_amount")
    )
    if average == 0.0 and (minimum or maximum):
        if minimum and maximum:
            average = (minimum + maximum) / 2
        else:
            average = maximum or minimum
    return minimum, maximum, average


def _clean_asset_identifier(identifier: object) -> str:
    return str(identifier).strip()


def _collect_in_scope_assets(program: dict, web_only: bool) -> list[str]:
    targets = program.get("targets") or {}
    in_scope = targets.get("in_scope") or []
    domains: list[str] = []
    for asset in in_scope:
        if not isinstance(asset, dict):
            continue
        asset_type = str(asset.get("asset_type", "")).upper()
        if web_only and asset_type not in WEB_ASSET_TYPES:
            continue
        identifier = asset.get("asset_identifier")
        if not identifier:
            continue
        cleaned = _clean_asset_identifier(identifier)
        if cleaned and cleaned not in domains:
            domains.append(cleaned)
    return domains


def filter_targets(
    programs: list[dict],
    min_bounty: float = 0,
    web_only: bool = True,
    search_query: str = "",
    min_response_rate: float = 0,
) -> list[dict]:
    query = search_query.strip().lower()
    results: list[dict] = []
    for program in programs:
        if not isinstance(program, dict):
            continue
        if not program.get("offers_bounties"):
            continue
        name = str(program.get("name", "")).strip()
        handle = str(program.get("handle", "")).strip()
        domains = _collect_in_scope_assets(program, web_only)
        if web_only and not domains:
            continue
        if query:
            match_name = query in name.lower()
            match_handle = query in handle.lower()
            match_domain = any(query in domain.lower() for domain in domains)
            if not (match_name or match_handle or match_domain):
                continue
        response_rate = _coerce_float(program.get("response_efficiency_percentage"))
        if min_response_rate > 0 and response_rate < min_response_rate:
            continue
        minimum, maximum, average = _extract_bounty_range(program)
        if min_bounty > 0 and maximum < min_bounty:
            continue
        program_url = str(program.get("url", "")).strip() or f"https://hackerone.com/{handle}"
        has_wildcard = any("*" in domain for domain in domains)
        results.append(
            {
                "name": name or handle,
                "handle": handle,
                "url": program_url,
                "bounty_min": minimum,
                "bounty_max": maximum,
                "average_bounty": average,
                "response_efficiency": response_rate,
                "in_scope_domains": domains,
                "has_wildcard": has_wildcard,
                "domain_count": len(domains),
                "managed_program": bool(program.get("managed_program")),
                "average_time_to_first_program_response": _coerce_float(
                    program.get("average_time_to_first_program_response")
                ),
            }
        )
    return results


ENRICHMENT_CACHE_PATH = Path("data/hackerone_enriched.json")
ENRICHMENT_WORKERS = 8
RESPONSE_EFFICIENCY_WEIGHT = 35
LOW_COMPETITION_POINTS = 25
MEDIUM_COMPETITION_POINTS = 15
ELEVATED_COMPETITION_POINTS = 10
HIGH_COMPETITION_POINTS = 5
WILDCARD_POINTS = 15
DOMAIN_BREADTH_POINTS = 5
MANAGED_PROGRAM_POINTS = 10
RESOLVED_REPORTS_POINTS = 10
DOMAIN_BREADTH_THRESHOLD = 5
LOW_COMPETITION_LIMIT = 100
MEDIUM_COMPETITION_LIMIT = 300
HIGH_COMPETITION_LIMIT = 500


def _competition_points(researcher_count: int) -> int:
    if researcher_count <= 0:
        return LOW_COMPETITION_POINTS
    if researcher_count < LOW_COMPETITION_LIMIT:
        return LOW_COMPETITION_POINTS
    if researcher_count <= MEDIUM_COMPETITION_LIMIT:
        return MEDIUM_COMPETITION_POINTS
    if researcher_count <= HIGH_COMPETITION_LIMIT:
        return ELEVATED_COMPETITION_POINTS
    return HIGH_COMPETITION_POINTS


def _tier_for_score(score: int) -> str:
    if score >= 85:
        return "S"
    if score >= 70:
        return "A"
    if score >= 50:
        return "B"
    return "C"


def _has_wildcard(program: dict) -> bool:
    if program.get("has_wildcard"):
        return True
    for domain in program.get("in_scope_domains") or []:
        if "*" in str(domain):
            return True
    return False


def calculate_opportunity_score(program: dict) -> dict:
    response_rate = _coerce_float(
        program.get("response_efficiency")
        or program.get("response_efficiency_percentage")
    )
    researcher_count = int(_coerce_float(program.get("researcher_count")))
    resolved_reports = int(_coerce_float(program.get("resolved_report_count")))
    managed_program = bool(program.get("managed_program"))
    domains = program.get("in_scope_domains") or []
    domain_count = len(domains)
    wildcard_present = _has_wildcard(program)

    response_points = round(min(response_rate, 100.0) / 100.0 * RESPONSE_EFFICIENCY_WEIGHT)
    competition_points = _competition_points(researcher_count)
    scope_points = (WILDCARD_POINTS if wildcard_present else 0)
    if domain_count >= DOMAIN_BREADTH_THRESHOLD:
        scope_points += DOMAIN_BREADTH_POINTS
    quality_points = (MANAGED_PROGRAM_POINTS if managed_program else 0)
    if resolved_reports > 0:
        quality_points += RESOLVED_REPORTS_POINTS

    score = max(0, min(100, response_points + competition_points + scope_points + quality_points))
    tier = _tier_for_score(score)

    highlights: list[str] = []
    if response_rate > 0:
        highlights.append(f"{round(response_rate)}% response efficiency")
    if researcher_count and researcher_count < LOW_COMPETITION_LIMIT:
        highlights.append("low researcher competition")
    elif researcher_count > HIGH_COMPETITION_LIMIT:
        highlights.append("crowded program")
    if wildcard_present:
        highlights.append("wildcard scope")
    elif domain_count >= DOMAIN_BREADTH_THRESHOLD:
        highlights.append(f"{domain_count} in-scope domains")
    if managed_program:
        highlights.append("HackerOne managed triage")
    if resolved_reports > 0:
        highlights.append(f"{resolved_reports} resolved reports")

    reason = f"Tier {tier} ({score}/100): " + (", ".join(highlights) if highlights else "limited public signal")

    return {
        "score": score,
        "tier": tier,
        "recommendation_reason": reason,
    }


def _load_enrichment_cache(cache_path: Path) -> dict:
    cache_path = Path(cache_path)
    if not cache_path.exists():
        return {}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _save_enrichment_cache(cache_path: Path, data: dict) -> None:
    cache_path = Path(cache_path)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        return


def _derive_enrichment(target: dict, cached: dict) -> dict:
    researcher_count = int(_coerce_float(cached.get("researcher_count")))
    resolved_reports = int(_coerce_float(cached.get("resolved_report_count")))
    time_to_first_response = _coerce_float(
        cached.get("time_to_first_response")
        or target.get("average_time_to_first_program_response")
    )
    managed_program = bool(cached.get("managed_program", target.get("managed_program", False)))
    return {
        "researcher_count": researcher_count,
        "resolved_report_count": resolved_reports,
        "time_to_first_response": time_to_first_response,
        "managed_program": managed_program,
    }


def enrich_target(target: dict, cache: dict) -> dict:
    cached = cache.get(target.get("handle", ""), {})
    if not isinstance(cached, dict):
        cached = {}
    enrichment = _derive_enrichment(target, cached)
    enriched = dict(target)
    enriched.update(enrichment)
    scoring = calculate_opportunity_score(enriched)
    enriched["opportunity_score"] = scoring["score"]
    enriched["tier"] = scoring["tier"]
    enriched["recommendation_reason"] = scoring["recommendation_reason"]
    return enriched


def enrich_targets(targets: list[dict], cache_path: Path = ENRICHMENT_CACHE_PATH) -> list[dict]:
    if not targets:
        return []
    cache = _load_enrichment_cache(cache_path)
    enriched: list[dict] = [{} for _ in targets]
    with ThreadPoolExecutor(max_workers=ENRICHMENT_WORKERS) as executor:
        future_to_index = {
            executor.submit(enrich_target, target, cache): index
            for index, target in enumerate(targets)
        }
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            enriched[index] = future.result()
    return enriched


def sort_targets(targets: list[dict], sort_by: str = "opportunity") -> list[dict]:
    if sort_by == "response":
        return sorted(targets, key=lambda item: _coerce_float(item.get("response_efficiency")), reverse=True)
    if sort_by == "name":
        return sorted(targets, key=lambda item: str(item.get("name", "")).lower())
    return sorted(targets, key=lambda item: item.get("opportunity_score", 0), reverse=True)


def filter_by_tier(targets: list[dict], tier: str) -> list[dict]:
    normalized = (tier or "").strip().upper()
    if normalized not in {"S", "A", "B", "C"}:
        return targets
    return [target for target in targets if target.get("tier") == normalized]
