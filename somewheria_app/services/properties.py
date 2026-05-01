import base64
import concurrent.futures
import copy
import json
import os
import re
import secrets
import threading
import time
from io import BytesIO

import requests
from PIL import Image, ImageOps, UnidentifiedImageError
from flask import url_for
from werkzeug.utils import secure_filename

from .console import get_console_logger


ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}
MAX_IMAGE_BYTES = 16 * 1024 * 1024
PROPERTY_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class UploadValidationError(ValueError):
    pass


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
}


class PropertyService:
    def __init__(self, config, notifications) -> None:
        self.config = config
        self.notifications = notifications
        self.logger = get_console_logger("properties")
        self.cache = []
        self.cache_lock = threading.Lock()
        self.refresh_thread = None

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
        latest = self.fetch_all_properties()
        with self.cache_lock:
            self.cache = latest
        self.logger.info("Cache refreshed with %s properties", len(self.cache))

    def get_cached_properties(self) -> list[dict]:
        with self.cache_lock:
            return copy.deepcopy(self.cache)

    def get_property(self, property_id: str):
        with self.cache_lock:
            for property_info in self.cache:
                if property_info.get("id") == property_id:
                    return copy.deepcopy(property_info)
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
        thread = threading.Thread(target=self._refresh_with_change_log, args=(actor_email,), daemon=True)
        thread.start()

    def _refresh_with_change_log(self, actor_email: str) -> None:
        try:
            latest_properties = self.fetch_all_properties()
            with self.cache_lock:
                current_snapshot = copy.deepcopy(self.cache)
                if json.dumps(current_snapshot, sort_keys=True) == json.dumps(latest_properties, sort_keys=True):
                    self.logger.info("Refresh completed with no property changes")
                    return
                log_details = self._build_change_log(current_snapshot, latest_properties)
                self.cache = latest_properties
            self.notifications.log_site_change(actor_email, "properties_cache_updated", log_details)
            self.logger.info(
                "Refresh applied: +%s / -%s / changed %s properties",
                len(log_details["added_ids"]),
                len(log_details["removed_ids"]),
                len(log_details["changed"]),
            )
        except Exception as exc:
            self.logger.error("On-demand refresh failed: %s", exc)

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
        return [property_info for property_info in results if property_info]

    def _fetch_property_ids(self) -> list[str]:
        try:
            response = requests.get(f"{self.config.api_base_url}/propertiesforrent", timeout=20)
            response.raise_for_status()
            return response.json().get("property_ids", [])
        except Exception:
            return []

    def fetch_property_record(self, property_id: str):
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
            photo_urls = self._safe_json(f"{self.config.api_base_url}/properties/{property_id}/photos", [])
            thumbnail_url = self._safe_json(
                f"{self.config.api_base_url}/properties/{property_id}/thumbnail", None
            )
            details["photos"] = []
            for photo_url in photo_urls:
                encoded = self.get_base64_image_from_url(photo_url)
                if encoded:
                    details["photos"].append(encoded)
            details["thumbnail"] = thumbnail_url or (details["photos"][0] if details["photos"] else "")
            return self.normalize_property(details, property_id)
        except Exception as exc:
            self.logger.warning("Failed to fetch property %s: %s", property_id, exc)
            return None

    def _safe_json(self, url: str, default):
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception:
            return default

    def normalize_property(self, property_info: dict, property_id: str | None = None) -> dict:
        normalized = dict(property_info or {})
        if property_id:
            normalized["id"] = property_id
        normalized.setdefault("included_amenities", normalized.get("included_utilities", []))
        normalized.setdefault("bedrooms", "N/A")
        normalized.setdefault("bathrooms", "N/A")
        normalized.setdefault("rent", "N/A")
        normalized.setdefault("sqft", "N/A")
        normalized.setdefault("deposit", "N/A")
        normalized.setdefault("address", "N/A")
        normalized["description"] = normalized.get("description", "")
        normalized.setdefault("blurb", normalized["description"])
        normalized.setdefault("lease_length", "12 months")
        normalized.setdefault("name", "Property")
        normalized.setdefault("photos", [])
        if not isinstance(normalized["photos"], list):
            normalized["photos"] = []
        pets_allowed = normalized.get("pets_allowed", "Unknown")
        if isinstance(pets_allowed, bool):
            pets_allowed = "Yes" if pets_allowed else "No"
        elif "included_amenities" in normalized and any(
            "pet" in str(item).lower() for item in normalized["included_amenities"]
        ):
            pets_allowed = "Yes"
        elif "description" in normalized and "pet" in normalized["description"].lower():
            pets_allowed = "Yes"
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
        normalized.setdefault("thumbnail", normalized["photos"][0] if normalized["photos"] else "")
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
                processed = self.letterbox_to_16_9(ImageOps.exif_transpose(image).convert("RGB"))
                out = BytesIO()
                processed.save(out, format="JPEG")
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
            "pets_allowed": form.get("pets_allowed", "Unknown") == "Yes",
            "blurb": form.get("blurb", ""),
            "description": form.get("description", ""),
            "included_amenities": amenities,
        }

    def create_property(self, form, actor_email: str) -> str:
        payload = self.property_payload_from_form(form)
        response = requests.post(f"{self.config.api_base_url}/properties", json=payload, timeout=20)
        response.raise_for_status()
        new_id = response.json().get("id") or response.json().get("property_id") or ""
        self.notifications.log_site_change(
            actor_email,
            "property_created",
            {"property_id": new_id, "address": payload.get("address")},
        )
        self.trigger_background_refresh(actor_email)
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
            }
        )
        amenities = list(form.getlist("amenities"))
        if updated_property["custom_amenities"]:
            amenities.extend(
                item.strip()
                for item in updated_property["custom_amenities"].split(",")
                if item.strip()
            )
        updated_property["included_amenities"] = amenities

        update_payload = {
            "name": updated_property.get("name"),
            "address": updated_property.get("address"),
            "rent": updated_property.get("rent"),
            "deposit": updated_property.get("deposit"),
            "sqft": updated_property.get("sqft", ""),
            "bedrooms": updated_property.get("bedrooms"),
            "bathrooms": updated_property.get("bathrooms"),
            "lease_length": updated_property.get("lease_length"),
            "pets_allowed": updated_property.get("pets_allowed") == "Yes",
            "blurb": updated_property.get("blurb"),
            "description": updated_property.get("description"),
            "included_amenities": updated_property.get("included_amenities", []),
        }
        # Verify the upstream accepted the change before logging it as an
        # update — otherwise a 4xx/5xx silently records a phantom change.
        response = requests.put(
            f"{self.config.api_base_url}/properties/{property_id}/details",
            json=update_payload,
            timeout=20,
        )
        response.raise_for_status()
        self.notifications.log_site_change(actor_email, "property_updated", {"property_id": property_id})
        self.trigger_background_refresh(actor_email)

    def delete_property(self, property_id: str, actor_email: str) -> None:
        response = requests.delete(f"{self.config.api_base_url}/properties/{property_id}", timeout=20)
        if response.status_code not in (200, 204):
            raise RuntimeError(f"Remote API responded {response.status_code}: {response.text}")
        with self.cache_lock:
            self.cache = [item for item in self.cache if item.get("id") != property_id]
        self.notifications.log_site_change(actor_email, "property_deleted", {"property_id": property_id})

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
                probe.verify()
        except (UnidentifiedImageError, Exception) as exc:
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
        # url_root is taken from Host header and can be spoofed, so we only
        # forward the stable relative URL to the upstream API.
        absolute_url = relative_url
        try:
            requests.post(
                f"{self.config.api_base_url}/properties/{property_id}/photos",
                json={"image_url": absolute_url},
                timeout=20,
            )
        except Exception as exc:
            self.logger.warning("Failed to associate uploaded image with property %s: %s", property_id, exc)
        self.trigger_background_refresh(actor_email)
        self.notifications.notify_image_edit([absolute_url])
        self.notifications.log_site_change(
            actor_email,
            "image_added",
            {"property_id": property_id, "image": absolute_url},
        )
        return relative_url

    def fetch_live_property_name(self, property_id: str) -> str | None:
        try:
            property_info = requests.get(
                f"{self.config.api_base_url}/properties/{property_id}/details",
                timeout=10,
            ).json()
            return property_info.get("name", "(Unknown Property)")
        except Exception:
            return None
