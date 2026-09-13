import json
from dataclasses import dataclass
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import UUID

from django.db import transaction

from .models import NovaPoshtaArea, NovaPoshtaRegion


class NovaPoshtaError(Exception):
    """Ошибка загрузки справочника Новой почты."""


@dataclass(frozen=True)
class AreaSyncResult:
    created: int
    updated: int
    unchanged: int


def _fetch_catalog(method, properties):
    request = Request(
        "https://api.novaposhta.ua/v2.0/json/",
        data=json.dumps({
            "modelName": "AddressGeneral",
            "calledMethod": method,
            "methodProperties": properties,
        }).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.load(response)
    except (URLError, TimeoutError, OSError) as exc:
        raise NovaPoshtaError("Не удалось связаться с Новой почтой. Повторите позже.") from exc
    except (ValueError, UnicodeError) as exc:
        raise NovaPoshtaError("Новая почта вернула некорректный JSON.") from exc

    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise NovaPoshtaError("Новая почта вернула ошибку при получении справочника.")
    data = payload.get("data")
    if not isinstance(data, list):
        raise NovaPoshtaError("Новая почта вернула некорректный справочник.")
    return data


def fetch_areas():
    data = _fetch_catalog("getSettlementAreas", {})
    if not data:
        raise NovaPoshtaError("Новая почта вернула пустой справочник областей.")

    areas = {}
    try:
        for item in data:
            ref = UUID(item["Ref"])
            description = item["Description"]
            if not isinstance(description, str) or not description.strip() or len(description) > 255:
                raise ValueError("Invalid description")
            if ref in areas:
                raise ValueError("Duplicate ref")
            areas[ref] = description
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise NovaPoshtaError("Некорректные данные области в ответе Новой почты.") from exc
    return areas


def sync_areas():
    """Загрузить и проверить весь ответ до записи; отсутствующие области не удалять."""
    areas = fetch_areas()
    created = updated = unchanged = 0
    with transaction.atomic():
        for ref, description in areas.items():
            area, is_new = NovaPoshtaArea.objects.get_or_create(
                ref=ref, defaults={"description": description}
            )
            if is_new:
                created += 1
            elif area.description != description:
                area.description = description
                area.save(update_fields=["description"])
                updated += 1
            else:
                unchanged += 1
    return AreaSyncResult(created, updated, unchanged)


def fetch_regions(area_ref):
    data = _fetch_catalog("getSettlementCountryRegion", {"AreaRef": str(area_ref)})
    regions = {}
    try:
        for item in data:
            ref = UUID(item["Ref"])
            description = item["Description"]
            region_type = item["RegionType"]
            for value, limit in ((description, 255), (region_type, 100)):
                if not isinstance(value, str) or not value.strip() or len(value) > limit:
                    raise ValueError("Invalid region field")
            if ref in regions:
                raise ValueError("Duplicate ref")
            regions[ref] = {"description": description, "region_type": region_type}
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise NovaPoshtaError("Некорректные данные района в ответе Новой почты.") from exc
    return regions


def sync_regions(area=None):
    areas = [area] if area is not None else list(NovaPoshtaArea.objects.all())
    if not areas:
        raise NovaPoshtaError("Сначала обновите справочник областей Новой почты.")
    regions = {}
    for current_area in areas:
        try:
            loaded = fetch_regions(current_area.ref)
        except NovaPoshtaError as exc:
            raise NovaPoshtaError(f"Область «{current_area}»: {exc}") from exc
        for ref, values in loaded.items():
            if ref in regions:
                raise NovaPoshtaError("Один район получен для нескольких областей.")
            regions[ref] = {**values, "area_id": current_area.ref}

    created = updated = unchanged = 0
    # Сначала загружаем все области, чтобы ошибка API не оставила частичное обновление.
    with transaction.atomic():
        for ref, values in regions.items():
            region, is_new = NovaPoshtaRegion.objects.get_or_create(ref=ref, defaults=values)
            if is_new:
                created += 1
                continue
            changed = [field for field, value in values.items() if getattr(region, field) != value]
            if changed:
                for field in changed:
                    setattr(region, field, values[field])
                region.save(update_fields=changed)
                updated += 1
            else:
                unchanged += 1
    return AreaSyncResult(created, updated, unchanged)
