import base64
import concurrent.futures
import copy
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import urlparse

import requests
from PIL import Image, ImageOps
from flask import url_for
from werkzeug.utils import secure_filename

from .console import get_console_logger


ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}
MAX_IMAGE_BYTES = 16 * 1024 * 1024
# Bound the decoded raster so a hostile / malformed file can't trigger a
# multi-gigabyte allocation. A highly-compressed PNG well under MAX_IMAGE_BYTES
# can decode to tens of thousands of pixels per side, and letterboxing on
# extreme aspect ratios amplifies that further. 24MP comfortably exceeds any
# real property photo from a phone or DSLR.
MAX_IMAGE_PIXELS = 24_000_000
# Cap any single dimension so an extreme aspect ratio can't blow up the
# letterbox output even when total pixel count is within MAX_IMAGE_PIXELS.
MAX_IMAGE_DIMENSION = 6000
# Longest edge for gallery photos embedded (base64) in a property page. Full
# 24MP phone photos both blew past the letterbox pixel guard (so they were
# dropped from the listing entirely) and bloated the inline payload to several
# MB each. 1200px is plenty for a web gallery; combined with GALLERY_JPEG_QUALITY
# it keeps a multi-photo page to a couple of MB instead of ~8 MB.
MAX_GALLERY_EDGE = 1200
GALLERY_JPEG_QUALITY = 80
PROPERTY_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Tighten Pillow's own decompression-bomb threshold so even if our explicit
# checks miss a code path, decoding raises rather than silently allocating.
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


class UploadValidationError(ValueError):
    pass


class UpstreamUnavailable(RuntimeError):
    """Raised when the upstream property API cannot be reached.

    Distinguishes a transient network/HTTP failure from a legitimate
    "upstream has zero properties" response. Callers of
    :meth:`PropertyService.refresh_cache` rely on this exception to
    preserve the existing cache during brief outages — without it a 5xx
    or timeout would silently blank the listing page until the next
    successful refresh.
    """


def _ensure_safe_image_dimensions(image: Image.Image) -> None:
    width, height = image.size
    if width <= 0 or height <= 0:
        raise UploadValidationError("Image has invalid dimensions.")
    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        raise UploadValidationError(
            f"Image dimensions exceed {MAX_IMAGE_DIMENSION}px limit."
        )
    if width * height > MAX_IMAGE_PIXELS:
        raise UploadValidationError("Image pixel count exceeds safe limit.")


BLANK_PROPERTY = {
    "name": "",
    "address": "",
    "rent": "",
    "deposit": "",
    "bedrooms": "",
    "bathrooms": "",
    "lease_length": "12 months",
    "pets_allowed": "Unknown",
    "ada_accessible": "Unknown",
    "blurb": "",
    "description": "",
    "included_amenities": [],
    "custom_amenities": "",
    "photos": [],
    "thumbnail": "",
    "tour_url": "",
}


# Allow only http(s) URLs for embedded 3D tour iframes. javascript:, data:, vbscript:,
# and file: schemes can execute script in the parent context or read local resources;
# a relative or empty value is rejected so we never embed an unintended target.
_ALLOWED_TOUR_SCHEMES = {"http", "https"}


def sanitize_tour_url(raw: str) -> str:
    """Return a safe http(s) tour URL or "" if the value is missing/unsafe.

    Capped at 2048 chars at the boundary so a hostile form post can't smuggle a
    multi-megabyte string through to upstream / templates.
    """
    if not isinstance(raw, str):
        return ""
    value = raw.strip()[:2048]
    if not value:
        return ""
    # Reject anything below 0x20 (NUL, TAB, CR, LF, …) or DEL (0x7F). Browsers
    # and urllib disagree on how to handle embedded control characters: urlparse
    # silently strips a stray "\n" or "\t" from the netloc, but the raw value we
    # return still carries it. Embedding that in an iframe ``src`` attribute is
    # a known URL-spoofing vector — different parsers cut the URL at different
    # points. Rejecting at the boundary keeps the value byte-identical to what
    # both we and the browser will parse.
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        return ""
    # Reject backslashes anywhere in the URL. Python's urlparse keeps them
    # in the path/netloc as-is, but some browsers normalize "\" to "/" before
    # parsing, which lets ``https://evil.com\@target.com`` resolve to either
    # ``evil.com`` or ``target.com`` depending on the consumer. Refusing them
    # avoids the ambiguity entirely.
    if "\\" in value:
        return ""
    try:
        parsed = urlparse(value)
    except ValueError:
        return ""
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_TOUR_SCHEMES:
        return ""
    if not parsed.netloc:
        return ""
    # Reject URLs that embed credentials in the authority section
    # (``https://user:pass@host/``). Modern browsers warn or refuse outright,
    # but an iframe ``src`` with userinfo can still leak through and is never
    # legitimate for a 3D-tour embed — Matterport / Kuula links never use it.
    if "@" in parsed.netloc:
        return ""
    return value


# Canonical amenity labels and the variant spellings that mean the same
# thing. Mirrors the checkbox aliases in templates/edit_listing.html — the
# edit form pre-checks "Washer/Dryer" when a stored variant like "laundry"
# matches, then submits the canonical value, so without write-time
# canonicalization every edit-save round-trip appends a duplicate
# ("Washer/Dryer" + "washer dryer") to included_amenities.
AMENITY_ALIASES = {
    "Air Conditioning": ["air conditioning", "ac", "a/c"],
    "Heating": ["heating", "heat"],
    "Dishwasher": ["dishwasher"],
    "Microwave": ["microwave"],
    "Refrigerator": ["refrigerator", "fridge"],
    "Washer/Dryer": ["washer/dryer", "washer dryer", "laundry"],
    "Washer/Dryer Hookup": ["washer/dryer hookup", "washer dryer hookup", "laundry hookup"],
    "Balcony/Deck": ["balcony", "deck", "balcony/deck"],
    "Parking": ["parking", "garage", "carport"],
    "Gym": ["gym", "fitness", "fitness center"],
    "Pool": ["pool", "swimming pool"],
    "Pet Friendly": ["pet friendly", "pets allowed", "pets ok", "pet"],
    "Furnished": ["furnished"],
    "Hardwood Floors": ["hardwood floors", "wood floors", "hardwood"],
    "Walk-in Closet": ["walk-in closet", "walk in closet", "walkin closet"],
    "In-unit Laundry": ["in-unit laundry", "in unit laundry", "laundry in unit"],
    "Central Air": ["central air", "central a/c", "central ac"],
    "Fireplace": ["fireplace"],
    "Garden": ["garden", "yard"],
    "Storage": ["storage", "storage unit"],
    "Snow Removal": ["snow removal", "snow"],
    "Lawn Mowing Service": ["lawn mowing service", "lawn service", "lawn care", "mowing", "landscaping"],
    "Common Area Cleaning Service": ["common area cleaning service", "common area cleaning", "cleaning service"],
    "Water": ["water"],
    "Trash": ["trash", "garbage", "refuse"],
    "Sewer": ["sewer", "sewage"],
}

_AMENITY_CANONICAL_BY_ALIAS = {
    alias: canonical
    for canonical, aliases in AMENITY_ALIASES.items()
    for alias in [canonical.lower(), *aliases]
}


def canonicalize_amenities(items) -> list[str]:
    """Map amenity variants to their canonical label and drop duplicates,
    case-insensitively, preserving first-seen order. Unrecognized custom
    amenities pass through trimmed but otherwise unchanged."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items or []:
        text = str(item).strip()
        if not text:
            continue
        canonical = _AMENITY_CANONICAL_BY_ALIAS.get(text.lower(), text)
        key = canonical.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(canonical)
    return result


class PropertyService:
    # Concurrent /for-rent and /for-rent.json hits each used to fire their
    # own upstream fanout (one ID listing + per-property detail/photos), so
    # a small burst multiplied AWS API Gateway / Lambda cost N-fold. The
    # synchronous refresh now serializes on a single lock, and any caller
    # arriving within REFRESH_COALESCE_SECONDS of the previous attempt
    # piggybacks on the already-fresh cache without making upstream calls.
    # The window is small enough that the cache stays effectively
    # request-fresh and large enough to absorb a realistic burst.
    REFRESH_COALESCE_SECONDS = 2.0

    def __init__(self, config, notifications, zillow=None, storage=None) -> None:
        self.config = config
        self.notifications = notifications
        # Optional dependency: when present, every successful mutation
        # enqueues a publish-only sync to Zillow. Wrapped in try/except at
        # each call site so a Zillow failure NEVER blocks the admin op —
        # the site is the source of truth.
        self.zillow = zillow
        # Optional dependency: persists which listings an admin has
        # deactivated (hidden from the public site but kept upstream). When
        # absent (some unit-test fixtures), nothing is hidden.
        self.storage = storage
        self.logger = get_console_logger("properties")
        self.cache = []
        self.cache_lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._last_refresh_monotonic = 0.0
        # Guards ``_background_refresh_active``. Held only for the brief
        # check-and-set in ``trigger_background_refresh`` and the reset
        # in ``_refresh_with_change_log``'s finally — never while doing
        # actual work — so a long-running upstream fanout can't block
        # other call sites from observing the flag.
        self._background_refresh_flag_lock = threading.Lock()
        self._background_refresh_active = False
        # Lightweight upstream-API health, surfaced on the admin status page.
        # Guarded by ``_refresh_lock`` (only written inside refresh_cache).
        self._last_success_at = None      # wall-clock of last successful refresh
        self._last_refresh_ok = None      # bool of last attempt; None = never run
        self._last_refresh_error = None   # error string from the last failure
        self._last_refresh_seconds = None # duration of the last attempt
        self.refresh_thread = None

    def _safe_zillow_publish(self, method_name: str, *args, **kwargs) -> None:
        if self.zillow is None:
            return
        try:
            getattr(self.zillow, method_name)(*args, **kwargs)
        except Exception as exc:
            # Zillow is publish-only and best-effort. Log and move on; the
            # admin operation has already succeeded against the source of
            # truth (the upstream property API).
            self.logger.warning("Zillow %s enqueue failed: %s", method_name, exc)

    def start_background_refresh(self) -> None:
        if self.refresh_thread and self.refresh_thread.is_alive():
            return
        self.refresh_thread = threading.Thread(target=self._periodic_refresh, daemon=True)
        self.refresh_thread.start()

    def _periodic_refresh(self) -> None:
        while True:
            try:
                self.refresh_cache()
            except Exception as exc:
                self.logger.error("Background refresh failed: %s", exc)
            time.sleep(self.config.cache_refresh_interval)

    def refresh_cache(self) -> None:
        # Serialize concurrent refreshes so at most one upstream fanout is
        # in flight per process. Followers that arrive while the leader is
        # still fetching queue on the lock; once the leader returns, each
        # follower sees the just-updated timestamp and skips its own
        # fanout. ``fetch_all_properties`` propagates ``UpstreamUnavailable``
        # from ``_fetch_property_ids`` on a network / HTTP failure; we let
        # it bubble up so the caller (route handler, periodic worker) can
        # decide what to do. The local ``self.cache`` is only reassigned
        # *after* a successful fetch, so a raise here preserves the
        # previous cache rather than blanking the listings.
        with self._refresh_lock:
            if (
                time.monotonic() - self._last_refresh_monotonic
                < self.REFRESH_COALESCE_SECONDS
            ):
                return
            started = time.monotonic()
            try:
                latest = self.fetch_all_properties()
                with self.cache_lock:
                    self.cache = latest
                self.logger.info("Cache refreshed with %s properties", len(self.cache))
                self._last_refresh_ok = True
                self._last_refresh_error = None
                self._last_success_at = datetime.now(timezone.utc)
            except Exception as exc:
                # Record the failure for the status page, then re-raise so the
                # caller's UpstreamUnavailable fallback still serves the
                # existing cache rather than blanking the listings.
                self._last_refresh_ok = False
                self._last_refresh_error = str(exc)
                raise
            finally:
                # Record the attempt time on both success AND failure: an
                # upstream outage means queued followers should serve the
                # existing cache via the route's UpstreamUnavailable
                # fallback rather than each retry the dead endpoint in
                # series. Take a single monotonic reading so the recorded
                # completion timestamp and duration derive from the same
                # clock sample — otherwise the follower coalesce check
                # (``time.monotonic() - _last_refresh_monotonic``) and the
                # admin-status duration would be computed from monotonic
                # values a few operations apart.
                completed_at = time.monotonic()
                self._last_refresh_monotonic = completed_at
                self._last_refresh_seconds = completed_at - started

    def get_cached_properties(self) -> list[dict]:
        with self.cache_lock:
            return copy.deepcopy(self.cache)

    def property_count(self) -> int:
        # Callers that only need the length (admin status/dashboard metrics)
        # should reach for this instead of ``len(get_cached_properties())``.
        # ``get_cached_properties`` deep-copies the full cache — including the
        # base64-encoded photo payload attached to every property — which can
        # be tens of MB per call for a routine dashboard render.
        with self.cache_lock:
            return len(self.cache)

    def get_cache_health(self) -> dict:
        """Snapshot of upstream-API refresh health for the admin status page.

        ``age_seconds`` is how long ago the cache data was last successfully
        refreshed from the API Gateway/Lambda backend; ``None`` until the
        first successful refresh in this process.
        """
        last_success = self._last_success_at
        age_seconds = None
        if last_success is not None:
            age_seconds = (datetime.now(timezone.utc) - last_success).total_seconds()
        return {
            "last_attempt_ok": self._last_refresh_ok,
            "last_success_at": last_success,
            "age_seconds": age_seconds,
            "last_error": self._last_refresh_error,
            "last_refresh_seconds": self._last_refresh_seconds,
        }

    def get_property(self, property_id: str):
        with self.cache_lock:
            for property_info in self.cache:
                if property_info.get("id") == property_id:
                    return copy.deepcopy(property_info)
        return None

    def get_property_name(self, property_id: str) -> str | None:
        # Callers that only need the display name (ticket submissions labeling
        # a property, etc.) should reach for this instead of get_property().
        # ``get_property`` deep-copies the whole record — including the
        # base64-encoded photo payload, which can be tens of MB — just so the
        # caller can read a single string field. Returning the name directly
        # from the cache under the lock avoids that copy entirely.
        with self.cache_lock:
            for property_info in self.cache:
                if property_info.get("id") == property_id:
                    name = property_info.get("name")
                    return name if isinstance(name, str) else None
        return None

    def serialize_properties(self, properties: list[dict]) -> list[dict]:
        serialized = []
        for property_info in properties:
            item = {}
            for key, value in property_info.items():
                item[key] = list(value) if isinstance(value, set) else value
            serialized.append(item)
        return serialized

    def trigger_background_refresh(self, actor_email: str = "anonymous") -> None:
        # Coalesce: at most one background refresh in flight per process.
        # ``/for-rent-refresh.json`` accepts up to 6 GETs per minute per IP
        # and admin mutations also trigger this path, so without the guard
        # a burst of callers each spawn their own daemon thread doing a
        # full 8-worker upstream fanout — stampeding the AWS API Gateway /
        # Lambda backend and piling up thread stacks for work that ends up
        # producing the same cache snapshot. One in-flight refresh's
        # result covers everyone arriving while it's running; subsequent
        # callers can be served from the now-fresh cache.
        with self._background_refresh_flag_lock:
            if self._background_refresh_active:
                return
            self._background_refresh_active = True
        thread = threading.Thread(target=self._refresh_with_change_log, args=(actor_email,), daemon=True)
        thread.start()

    def _refresh_with_change_log(self, actor_email: str) -> None:
        # Serialize with refresh_cache so an admin-triggered refresh and a
        # ``/for-rent`` synchronous refresh can't both fan out upstream
        # in parallel — and so a ``/for-rent`` arriving within the coalesce
        # window after this trigger completes reuses the freshly-populated
        # cache instead of re-firing the upstream fanout. Without this
        # serialization a burst of admin mutations (or hits on the
        # public ``/for-rent-refresh.json`` endpoint) could each spawn
        # their own concurrent fetch, multiplying API Gateway / Lambda
        # cost N-fold.
        try:
            with self._refresh_lock:
                if (
                    time.monotonic() - self._last_refresh_monotonic
                    < self.REFRESH_COALESCE_SECONDS
                ):
                    # Cache was just freshened by another caller; the admin
                    # operation that triggered us already wrote its own
                    # ``log_site_change`` entry, so there's no diff worth
                    # recording here. Skip the upstream fanout.
                    self.logger.info(
                        "On-demand refresh coalesced: cache freshened within %.1fs",
                        self.REFRESH_COALESCE_SECONDS,
                    )
                    return
                started = time.monotonic()
                try:
                    try:
                        latest_properties = self.fetch_all_properties()
                    except Exception as fetch_exc:
                        # Mirror refresh_cache's health-tracking on failure so
                        # /admin/status shows the outage regardless of which
                        # code path attempted the refresh. Without this,
                        # admin-triggered refreshes could silently regress the
                        # status page to "never attempted" or leave a stale
                        # "succeeded" from a much earlier /for-rent hit.
                        self._last_refresh_ok = False
                        self._last_refresh_error = str(fetch_exc)
                        raise
                    # Upstream reachable — record the success before mutating
                    # (or short-circuiting on) the local cache so the admin
                    # status page reflects real upstream connectivity even
                    # when the fetch happened to return an identical listing.
                    self._last_refresh_ok = True
                    self._last_refresh_error = None
                    self._last_success_at = datetime.now(timezone.utc)
                    with self.cache_lock:
                        # Compare the live cache to the latest fetch directly.
                        # The prior implementation serialized both sides to
                        # canonical JSON and compared the strings, which on a
                        # cache full of base64-encoded photo data allocates and
                        # walks tens of megabytes twice per admin-triggered
                        # refresh. ``list``/``dict`` equality in Python 3 is
                        # already order-independent for dict keys and walks
                        # element-by-element with a short-circuit on the first
                        # mismatch — same result, no large string allocation.
                        if self.cache == latest_properties:
                            self.logger.info("Refresh completed with no property changes")
                            return
                        # Build the diff BEFORE reassigning ``self.cache``.
                        # ``_build_change_log`` only reads ids/keys/values from
                        # its inputs (never mutates them), so passing the live
                        # cache list directly is safe and avoids deep-copying
                        # tens of MB of base64 photo data on every admin
                        # refresh. Same class of fix as commit 29e7f87 (drop
                        # the JSON re-serialize) and cc20983 (drop the
                        # deep-copy in property_count).
                        log_details = self._build_change_log(self.cache, latest_properties)
                        self.cache = latest_properties
                    self.notifications.log_site_change(actor_email, "properties_cache_updated", log_details)
                    self.logger.info(
                        "Refresh applied: +%s / -%s / changed %s properties",
                        len(log_details["added_ids"]),
                        len(log_details["removed_ids"]),
                        len(log_details["changed"]),
                    )
                finally:
                    # Record the attempt on both success AND failure so
                    # followers (a ``/for-rent`` hit or another trigger)
                    # arriving within the coalesce window don't immediately
                    # re-hit the same dead endpoint — mirrors the refresh_cache
                    # contract. Also stamp the observed fanout duration so
                    # the admin status page's "creeping toward 29s" check
                    # covers both refresh paths. Take a single monotonic
                    # reading so the completion timestamp and duration
                    # come from the same clock sample.
                    completed_at = time.monotonic()
                    self._last_refresh_monotonic = completed_at
                    self._last_refresh_seconds = completed_at - started
        except Exception as exc:
            self.logger.error("On-demand refresh failed: %s", exc)
        finally:
            with self._background_refresh_flag_lock:
                self._background_refresh_active = False

    def _build_change_log(self, current_properties: list[dict], latest_properties: list[dict]) -> dict:
        def ids_for(properties):
            return {prop.get("id") for prop in properties if prop and "id" in prop}

        def by_id(properties):
            return {prop.get("id"): prop for prop in properties if prop and "id" in prop}

        old_ids = ids_for(current_properties)
        new_ids = ids_for(latest_properties)
        changed = []
        current_by_id = by_id(current_properties)
        latest_by_id = by_id(latest_properties)
        for property_id in old_ids & new_ids:
            old = current_by_id[property_id]
            new = latest_by_id[property_id]
            diffs = [key for key in set(old.keys()).union(new.keys()) if old.get(key) != new.get(key)]
            if diffs:
                changed.append({"id": property_id, "fields": diffs})
        return {
            "added_ids": sorted(new_ids - old_ids),
            "removed_ids": sorted(old_ids - new_ids),
            "changed": changed,
            "old_count": len(current_properties),
            "new_count": len(latest_properties),
        }

    def fetch_all_properties(self) -> list[dict]:
        property_ids = self._fetch_property_ids()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(self.fetch_property_record, property_ids))
        successful = [property_info for property_info in results if property_info]
        # If the ID listing came back with properties but EVERY per-property
        # fetch returned None, that is a systemic outage of the details / photos
        # endpoint (5xx, timeout, DNS), not "every property was deleted." Raise
        # so refresh_cache leaves the existing cache in place — mirroring the
        # behavior already in place for _fetch_property_ids network failures.
        # Without this guard a brief details-endpoint outage blanks /for-rent
        # until the next successful refresh.
        if property_ids and not successful:
            raise UpstreamUnavailable(
                f"all {len(property_ids)} per-property fetches failed"
            )
        return successful

    def _fetch_property_ids(self) -> list[str]:
        # Network / HTTP failure raises ``UpstreamUnavailable`` so callers
        # (refresh_cache, _refresh_with_change_log) can leave the existing
        # cache intact instead of overwriting it with the empty list a bare
        # ``return []`` would produce on a 5xx or timeout.
        try:
            response = requests.get(f"{self.config.api_base_url}/propertiesforrent", timeout=20)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise UpstreamUnavailable(
                f"property id listing unavailable: {exc}"
            ) from exc
        # A malformed upstream payload (string, list, dict-of-non-list, …) must
        # not silently iterate as characters or unrelated keys downstream — that
        # would spray the upstream API with junk per-id requests. These shapes
        # are treated as "upstream returned no properties" rather than a
        # transient failure, matching the prior behavior for valid-but-empty
        # responses.
        if not isinstance(payload, dict):
            return []
        ids = payload.get("property_ids", [])
        if not isinstance(ids, list):
            return []
        return [item for item in ids if isinstance(item, str)]

    def fetch_property_record(self, property_id: str):
        # Defense in depth: refuse property IDs that don't match the expected
        # shape so a malformed upstream response can't smuggle traversal
        # sequences ("../") into our outbound HTTP paths.
        if not PROPERTY_ID_PATTERN.match(property_id or ""):
            self.logger.warning("Refusing to fetch property with invalid id: %r", property_id)
            return None
        try:
            details_response = requests.get(
                f"{self.config.api_base_url}/properties/{property_id}/details", timeout=10
            )
            # Without raise_for_status() a 4xx/5xx JSON error body would be
            # silently passed through as a property record.
            details_response.raise_for_status()
            details = details_response.json()
            if not isinstance(details, dict):
                self.logger.warning(
                    "Property %s details endpoint returned non-object payload; skipping",
                    property_id,
                )
                return None
            details = self._unwrap_details_payload(details, property_id)
            if not isinstance(details, dict):
                self.logger.warning(
                    "Property %s details endpoint returned an empty envelope; skipping",
                    property_id,
                )
                return None
            photo_urls = self._safe_json(
                f"{self.config.api_base_url}/properties/{property_id}/photos",
                [],
                expected_type=list,
            )
            thumbnail_url = self._safe_json(
                f"{self.config.api_base_url}/properties/{property_id}/thumbnail",
                None,
                expected_type=str,
            )
            # Keep the S3 photo URLs as-is rather than downloading and
            # base64-encoding every image here. The bucket objects are public
            # and the CSP allows https images, so the browser loads them
            # directly from S3. Encoding them inline turned this per-property
            # fetch into a multi-megabyte download+re-encode: with one listing
            # of ~16 full-res photos it made the /for-rent refresh take ~30s
            # and bloated the detail page to several MB. URLs make the refresh
            # a few small JSON calls and the detail page a few KB.
            details["photos"] = [url for url in photo_urls if isinstance(url, str) and url]
            details["thumbnail"] = thumbnail_url or (details["photos"][0] if details["photos"] else "")
            return self.normalize_property(details, property_id)
        except Exception as exc:
            self.logger.warning("Failed to fetch property %s: %s", property_id, exc)
            return None

    @staticmethod
    def _unwrap_details_payload(payload: dict, property_id: str) -> dict | None:
        # The details Lambda wraps the record as {"properties": [{...}]};
        # earlier deployments returned the object bare. Accept both, and when
        # the envelope carries several records prefer the one whose id matches.
        records = payload.get("properties")
        if not isinstance(records, list):
            return payload
        match = next(
            (r for r in records if isinstance(r, dict) and r.get("id") == property_id),
            None,
        )
        if match is None:
            match = next((r for r in records if isinstance(r, dict)), None)
        return match

    def _write_headers(self) -> dict:
        # The upstream API's write methods (POST/PUT/DELETE) require an API
        # key; reads are public. With no key configured the header is omitted
        # and the upstream rejects the write with 403.
        api_key = getattr(self.config, "api_key", "")
        return {"x-api-key": api_key} if api_key else {}

    def _safe_json(self, url: str, default, *, expected_type: type | tuple[type, ...] | None = None):
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return default
        if expected_type is not None and not isinstance(payload, expected_type):
            return default
        return payload

    def normalize_property(self, property_info: dict, property_id: str | None = None) -> dict:
        normalized = dict(property_info or {})
        if property_id:
            normalized["id"] = property_id
        normalized.setdefault("included_amenities", normalized.get("included_utilities", []))
        # Mirror the description/photos coercion: upstream occasionally returns
        # ``"included_amenities": null`` (or a misshaped non-list) for partially
        # filled listings. Without this guard the ``any()`` iteration in the
        # pets-inference branch below raises TypeError, fetch_property_record
        # swallows it as a generic "failed to fetch", and the property drops
        # out of the listing entirely.
        if not isinstance(normalized["included_amenities"], list):
            normalized["included_amenities"] = []
        # Defensive canonicalize+dedupe so records corrupted by earlier
        # append-without-dedupe saves ("Washer/Dryer" + "laundry") render one
        # badge per real amenity without needing a re-save.
        normalized["included_amenities"] = canonicalize_amenities(normalized["included_amenities"])
        # Replace any missing OR explicit-null scalar with the safe default.
        # ``setdefault`` alone leaves ``None`` in place, so a partially-filled
        # upstream listing (``{"name": null, "address": null, ...}``) would
        # render as the literal string "None" in templates and page titles.
        # Same class of bug as the description / included_amenities null fixes
        # above — here it doesn't crash, it just produces ugly UI.
        _scalar_defaults = (
            ("name", "Property"),
            ("address", "N/A"),
            ("rent", "N/A"),
            ("deposit", "N/A"),
            ("sqft", "N/A"),
            ("bedrooms", "N/A"),
            ("bathrooms", "N/A"),
            ("lease_length", "12 months"),
        )
        for key, default in _scalar_defaults:
            if normalized.get(key) is None:
                normalized[key] = default
        # Coerce a null / non-string description to "". Upstream occasionally
        # returns ``"description": null`` for partially-filled listings; without
        # this the ``.lower()`` call below raises AttributeError, fetch_property_record
        # swallows it as a generic "failed to fetch", and the property drops out
        # of the listing entirely.
        description = normalized.get("description")
        if not isinstance(description, str):
            description = ""
        normalized["description"] = description
        # blurb mirrors description as its fallback; coerce a null/non-string
        # blurb the same way so templates never render the literal "None".
        blurb = normalized.get("blurb", description)
        if not isinstance(blurb, str):
            blurb = description
        normalized["blurb"] = blurb
        normalized.setdefault("photos", [])
        if not isinstance(normalized["photos"], list):
            normalized["photos"] = []
        pets_allowed_raw = normalized.get("pets_allowed", "Unknown")
        if isinstance(pets_allowed_raw, bool):
            pets_allowed = "Yes" if pets_allowed_raw else "No"
        elif isinstance(pets_allowed_raw, str):
            lowered = pets_allowed_raw.strip().lower()
            if lowered in {"yes", "true", "1"}:
                pets_allowed = "Yes"
            elif lowered in {"no", "false", "0"}:
                pets_allowed = "No"
            else:
                # Only infer from amenities / description when the upstream
                # value is missing or unrecognized — otherwise a property
                # whose description reads "No pets allowed" would have its
                # explicit "No" flipped to "Yes" by the substring match.
                # Negations are checked first so that inference itself can
                # produce "No", and \bpet keeps carpet/trumpet/puppet from
                # matching at all.
                amenity_text = " ".join(
                    str(item).lower()
                    for item in normalized.get("included_amenities", [])
                )
                searchable = f"{normalized['description'].lower()} {amenity_text}"
                if re.search(
                    r"\bno\s+pets?\b|\bpets?\s+(?:are\s+)?not\s+(?:allowed|permitted)\b|\bpet[-\s]?free\b",
                    searchable,
                ):
                    pets_allowed = "No"
                elif re.search(r"\bpet", searchable):
                    pets_allowed = "Yes"
                else:
                    pets_allowed = "Unknown"
        else:
            pets_allowed = "Unknown"
        normalized["pets_allowed"] = pets_allowed
        ada_accessible = None
        for key in (
            "ada_accessible",
            "accessible",
            "accessibility",
            "is_accessible",
            "wheelchair_accessible",
        ):
            if key in normalized and normalized.get(key) not in (None, ""):
                ada_accessible = normalized.get(key)
                break

        if isinstance(ada_accessible, bool):
            ada_accessible = "Yes" if ada_accessible else "No"
        elif isinstance(ada_accessible, str):
            lowered_accessibility = ada_accessible.strip().lower()
            if lowered_accessibility in {"yes", "true", "1"}:
                ada_accessible = "Yes"
            elif lowered_accessibility in {"no", "false", "0"}:
                ada_accessible = "No"
            else:
                ada_accessible = "Unknown"
        else:
            ada_accessible = "Unknown"
        normalized["ada_accessible"] = ada_accessible
        # ``thumbnail`` may legitimately be missing OR null. Treat both as
        # "use the first photo if we have one, otherwise empty" so the
        # template's <img src="{{ property.thumbnail }}"> never gets None.
        if not normalized.get("thumbnail"):
            normalized["thumbnail"] = normalized["photos"][0] if normalized["photos"] else ""
        normalized["tour_url"] = sanitize_tour_url(normalized.get("tour_url", ""))
        return normalized

    def letterbox_to_16_9(self, image: Image.Image) -> Image.Image:
        target_ratio = 16 / 9
        width, height = image.size
        if height == 0:
            return image
        original_ratio = width / height
        if abs(original_ratio - target_ratio) < 1e-5:
            return image
        if original_ratio > target_ratio:
            new_width = width
            new_height = int(width / target_ratio)
        else:
            new_height = height
            new_width = int(height * target_ratio)
        # Defense in depth: an extreme aspect-ratio input (e.g. very tall)
        # whose own pixel count is within MAX_IMAGE_PIXELS can still letterbox
        # to a much larger canvas. Reject rather than allocate the buffer.
        if new_width * new_height > MAX_IMAGE_PIXELS:
            raise UploadValidationError(
                "Image aspect ratio would produce an oversized letterbox."
            )
        new_image = Image.new("RGB", (new_width, new_height), color=(0, 0, 0))
        new_image.paste(image, ((new_width - width) // 2, (new_height - height) // 2))
        return new_image

    def get_base64_image_from_url(self, url: str):
        try:
            # Stream the download with a hard byte cap so a misbehaving or
            # hostile upstream can't exhaust memory by serving an enormous
            # payload. Reject early via Content-Length when present, then
            # enforce the cap during streaming for chunked / unknown-length
            # responses.
            with requests.get(url, timeout=10, stream=True) as image_response:
                image_response.raise_for_status()
                advertised = image_response.headers.get("Content-Length")
                if advertised and advertised.isdigit() and int(advertised) > MAX_IMAGE_BYTES:
                    raise ValueError(f"Image exceeds {MAX_IMAGE_BYTES} byte limit")
                buffer = BytesIO()
                total = 0
                for chunk in image_response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_IMAGE_BYTES:
                        raise ValueError(f"Image exceeds {MAX_IMAGE_BYTES} byte limit")
                    buffer.write(chunk)
                raw = buffer.getvalue()
            with Image.open(BytesIO(raw)) as image:
                _ensure_safe_image_dimensions(image)
                oriented = ImageOps.exif_transpose(image).convert("RGB")
                # Downscale big source photos BEFORE letterboxing: a full-res
                # landscape photo (e.g. 6000x4000) letterboxes to a canvas that
                # exceeds MAX_IMAGE_PIXELS, so the old code dropped the photo
                # entirely. thumbnail() only ever shrinks and preserves aspect
                # ratio, so smaller images pass through untouched.
                oriented.thumbnail((MAX_GALLERY_EDGE, MAX_GALLERY_EDGE))
                processed = self.letterbox_to_16_9(oriented)
                out = BytesIO()
                processed.save(out, format="JPEG", quality=GALLERY_JPEG_QUALITY, optimize=True)
                encoded = base64.b64encode(out.getvalue()).decode("utf-8")
                return f"data:image/jpeg;base64,{encoded}"
        except Exception as exc:
            self.logger.warning("Could not process image %s: %s", url, exc)
            return None

    def property_payload_from_form(self, form) -> dict:
        custom_amenities = form.get("custom_amenities", "").strip()
        amenities = list(form.getlist("amenities"))
        if custom_amenities:
            amenities.extend(item.strip() for item in custom_amenities.split(",") if item.strip())
        return {
            "name": form.get("name", ""),
            "address": form.get("address", ""),
            "rent": form.get("rent", ""),
            "deposit": form.get("deposit", ""),
            "sqft": form.get("sqft", ""),
            "bedrooms": form.get("bedrooms", ""),
            "bathrooms": form.get("bathrooms", ""),
            "lease_length": form.get("lease_length", "12 months"),
            # Send the tri-state string as-is. The old ``== "Yes"`` boolean
            # collapse made "Unknown" unrepresentable — it round-tripped back
            # from upstream as False and displayed as "No". The upstream
            # column is text (its Lambda coerces booleans to "Yes"/"No").
            "pets_allowed": form.get("pets_allowed", "Unknown"),
            "blurb": form.get("blurb", ""),
            "description": form.get("description", ""),
            "included_amenities": canonicalize_amenities(amenities),
            "tour_url": sanitize_tour_url(form.get("tour_url", "")),
        }

    def create_property(self, form, actor_email: str) -> str:
        payload = self.property_payload_from_form(form)
        response = requests.post(
            f"{self.config.api_base_url}/properties",
            json=payload,
            headers=self._write_headers(),
            timeout=20,
        )
        response.raise_for_status()
        # Parse once and tolerate a non-dict / invalid-JSON body rather than
        # crashing the admin POST with AttributeError or JSONDecodeError.
        # The upstream creates the property either way; a missing id just
        # means the change-log entry won't carry it.
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            body = {}
        new_id = body.get("id") or body.get("property_id") or ""
        self.notifications.log_site_change(
            actor_email,
            "property_created",
            {"property_id": new_id, "address": payload.get("address")},
        )
        self.trigger_background_refresh(actor_email)
        self._safe_zillow_publish("publish_create", {**payload, "id": new_id})
        return new_id

    def update_property(self, property_id: str, form, actor_email: str) -> None:
        current_property = self.get_property(property_id)
        if not current_property:
            raise KeyError("Property not found")
        updated_property = dict(current_property)
        updated_property.update(
            {
                "name": form.get("name", current_property.get("name")),
                "address": form.get("address", current_property.get("address")),
                "rent": form.get("rent", current_property.get("rent")),
                "deposit": form.get("deposit", current_property.get("deposit")),
                "sqft": form.get("sqft", current_property.get("sqft", "")),
                "bedrooms": form.get("bedrooms", current_property.get("bedrooms")),
                "bathrooms": form.get("bathrooms", current_property.get("bathrooms")),
                "lease_length": form.get("lease_length", current_property.get("lease_length")),
                "pets_allowed": form.get("pets_allowed", current_property.get("pets_allowed")),
                "blurb": form.get("blurb", current_property.get("blurb")),
                "description": form.get("description", current_property.get("description")),
                "custom_amenities": form.get("custom_amenities", "").strip(),
                "tour_url": sanitize_tour_url(
                    form.get("tour_url", current_property.get("tour_url", ""))
                ),
            }
        )
        amenities = list(form.getlist("amenities"))
        if updated_property["custom_amenities"]:
            amenities.extend(
                item.strip()
                for item in updated_property["custom_amenities"].split(",")
                if item.strip()
            )
        # Canonicalize + dedupe: the checkbox re-submits the canonical label
        # while the stored variant rides along in custom_amenities, so a raw
        # extend() re-adds "washer dryer" next to "Washer/Dryer" on every save.
        updated_property["included_amenities"] = canonicalize_amenities(amenities)

        update_payload = {
            "name": updated_property.get("name"),
            "address": updated_property.get("address"),
            "rent": updated_property.get("rent"),
            "deposit": updated_property.get("deposit"),
            "sqft": updated_property.get("sqft", ""),
            "bedrooms": updated_property.get("bedrooms"),
            "bathrooms": updated_property.get("bathrooms"),
            "lease_length": updated_property.get("lease_length"),
            # Tri-state string, same rationale as property_payload_from_form.
            "pets_allowed": updated_property.get("pets_allowed") or "Unknown",
            "blurb": updated_property.get("blurb"),
            "description": updated_property.get("description"),
            "included_amenities": updated_property.get("included_amenities", []),
            "tour_url": updated_property.get("tour_url", ""),
        }
        # Verify the upstream accepted the change before logging it as an
        # update — otherwise a 4xx/5xx silently records a phantom change.
        response = requests.put(
            f"{self.config.api_base_url}/properties/{property_id}/details",
            json=update_payload,
            headers=self._write_headers(),
            timeout=20,
        )
        response.raise_for_status()
        self.notifications.log_site_change(actor_email, "property_updated", {"property_id": property_id})
        self.trigger_background_refresh(actor_email)
        self._safe_zillow_publish("publish_update", {**update_payload, "id": property_id})

    def hidden_listing_ids(self) -> set[str]:
        if self.storage is None:
            return set()
        return set(self.storage.get_hidden_listing_ids())

    def is_listing_hidden(self, property_id: str) -> bool:
        return str(property_id) in self.hidden_listing_ids()

    def get_visible_properties(self) -> list[dict]:
        """Cached properties minus the ones an admin has deactivated.

        Public pages (/for-rent, sitemap) render this; Manage Listings keeps
        using get_cached_properties so admins still see hidden listings.
        """
        hidden = self.hidden_listing_ids()
        if not hidden:
            return self.get_cached_properties()
        return [
            item for item in self.get_cached_properties()
            if str(item.get("id")) not in hidden
        ]

    def set_listing_active(self, property_id: str, active: bool, actor_email: str) -> None:
        """Reversible unpublish: hide/show a listing on the public site.

        The upstream property record is untouched (its table has no
        active/status column) — the hidden set is site-local state, which is
        what makes this safely reversible, unlike delete_property.
        """
        if self.storage is None:
            raise RuntimeError("No storage service configured; cannot hide listings.")
        if not self.get_property(property_id):
            raise KeyError("Property not found")
        self.storage.set_listing_hidden(property_id, not active)
        self.notifications.log_site_change(
            actor_email,
            "property_reactivated" if active else "property_deactivated",
            {"property_id": property_id},
        )

    def delete_property(self, property_id: str, actor_email: str) -> None:
        # Validate the id at the boundary so a malformed value (e.g. "../foo"
        # routed through Flask's default <string> converter) can't smuggle
        # path segments into the outbound DELETE URL. ``update_property`` and
        # ``toggle_sale`` are implicitly protected because they look the id
        # up in the cache first; ``delete_property`` skips that lookup so the
        # check has to live here.
        if not PROPERTY_ID_PATTERN.match(property_id or ""):
            raise KeyError("Invalid property id.")
        response = requests.delete(
            f"{self.config.api_base_url}/properties/{property_id}",
            headers=self._write_headers(),
            timeout=20,
        )
        if response.status_code not in (200, 204):
            raise RuntimeError(f"Remote API responded {response.status_code}: {response.text}")
        with self.cache_lock:
            self.cache = [item for item in self.cache if item.get("id") != property_id]
        self.notifications.log_site_change(actor_email, "property_deleted", {"property_id": property_id})
        self._safe_zillow_publish("publish_delete", property_id)

    def toggle_sale(self, property_id: str, actor_email: str) -> None:
        property_info = self.get_property(property_id)
        if not property_info:
            raise KeyError("Property not found")
        new_status = not property_info.get("for_sale", False)
        # Apply upstream first; if the API rejects the change, we must NOT
        # update the local cache, otherwise the UI would show stale state.
        response = requests.put(
            f"{self.config.api_base_url}/properties/{property_id}/details",
            json={"for_sale": new_status},
            headers=self._write_headers(),
            timeout=20,
        )
        response.raise_for_status()
        with self.cache_lock:
            for item in self.cache:
                if item.get("id") == property_id:
                    item["for_sale"] = new_status
                    item["status"] = "For Sale" if new_status else "Active"
                    break
        self.notifications.log_site_change(
            actor_email,
            "property_toggle_sale",
            {"property_id": property_id, "for_sale": new_status},
        )
        self._safe_zillow_publish("publish_for_sale_toggle", property_id, new_status)

    def upload_image(self, property_id: str, uploaded_file, url_root: str, actor_email: str) -> str:
        if not PROPERTY_ID_PATTERN.match(property_id or ""):
            raise UploadValidationError("Invalid property id.")

        original_name = secure_filename(uploaded_file.filename or "")
        if not original_name or "." not in original_name:
            raise UploadValidationError("Unsupported file name.")
        ext = original_name.rsplit(".", 1)[1].lower()
        if ext not in ALLOWED_IMAGE_EXTENSIONS:
            raise UploadValidationError("Unsupported image type.")

        # Validate image content BEFORE persisting to disk so bogus uploads
        # never touch the upload directory.
        raw = uploaded_file.stream.read(MAX_IMAGE_BYTES + 1)
        if len(raw) == 0:
            raise UploadValidationError("Empty upload.")
        if len(raw) > MAX_IMAGE_BYTES:
            raise UploadValidationError("Image exceeds maximum allowed size.")
        try:
            with Image.open(BytesIO(raw)) as probe:
                _ensure_safe_image_dimensions(probe)
                probe.verify()
        except UploadValidationError:
            raise
        except Exception as exc:
            raise UploadValidationError("File is not a valid image.") from exc

        new_filename = f"{property_id}_{secrets.token_hex(8)}.{ext}"
        upload_dir = self.config.upload_dir.resolve()
        save_path = (upload_dir / new_filename).resolve()
        # Path-traversal guard: the resolved destination must stay inside the
        # configured upload directory.
        try:
            save_path.relative_to(upload_dir)
        except ValueError as exc:
            raise UploadValidationError("Invalid destination path.") from exc

        try:
            with Image.open(BytesIO(raw)) as image:
                processed = self.letterbox_to_16_9(ImageOps.exif_transpose(image).convert("RGB"))
                processed.save(save_path, format="JPEG" if ext in ("jpg", "jpeg") else image.format or "PNG")
        except Exception as exc:
            try:
                if save_path.exists():
                    os.unlink(save_path)
            except OSError:
                pass
            self.notifications.log_and_notify_error(
                "Image Processing Error", f"Failed to process uploaded image: {exc}"
            )
            raise UploadValidationError("Failed to process image.") from exc

        relative_url = url_for("static", filename=f"uploads/{new_filename}", _external=False)
        absolute_url = relative_url
        # Photos live in the upstream S3 bucket (the photos endpoint lists
        # s3://somewheriaweb/{property_id}/), so the processed image bytes
        # are shipped up as base64; the local static/uploads copy is only a
        # fallback while the next cache refresh picks up the S3 URL.
        try:
            with open(save_path, "rb") as fh:
                encoded = base64.b64encode(fh.read()).decode("ascii")
            assoc_response = requests.post(
                f"{self.config.api_base_url}/properties/{property_id}/photos",
                json={"filename": new_filename, "content_base64": encoded},
                headers=self._write_headers(),
                timeout=30,
            )
            # Without raise_for_status() a 4xx/5xx response is silently
            # swallowed: the local file is saved and the change-log records
            # a successful "image_added", but the upstream never stores the
            # photo, so the next cache refresh blanks it from the listing.
            # Treat the HTTP error like a connection failure and surface a
            # warning so the operator can investigate.
            assoc_response.raise_for_status()
            uploaded = assoc_response.json()
            if isinstance(uploaded, dict) and uploaded.get("url"):
                absolute_url = uploaded["url"]
        except Exception as exc:
            self.logger.warning("Failed to upload image for property %s upstream: %s", property_id, exc)
        self.trigger_background_refresh(actor_email)
        self.notifications.notify_image_edit([absolute_url])
        self.notifications.log_site_change(
            actor_email,
            "image_added",
            {"property_id": property_id, "image": absolute_url},
        )
        return relative_url

    def fetch_live_property_name(self, property_id: str) -> str | None:
        # Validate the id at the boundary so unsanitized values can't smuggle
        # path-traversal sequences ("../") into the outbound URL.
        if not PROPERTY_ID_PATTERN.match(property_id or ""):
            return None
        try:
            response = requests.get(
                f"{self.config.api_base_url}/properties/{property_id}/details",
                timeout=10,
            )
            # Without raise_for_status() a 404/5xx JSON error body would be
            # treated as a real property and the caller would proceed as if
            # the property existed.
            response.raise_for_status()
            property_info = response.json()
        except Exception:
            return None
        if not isinstance(property_info, dict):
            return None
        property_info = self._unwrap_details_payload(property_info, property_id)
        if not isinstance(property_info, dict):
            return None
        name = property_info.get("name")
        if not isinstance(name, str) or not name.strip():
            return None
        return name
