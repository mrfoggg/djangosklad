import json
from dataclasses import dataclass
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import NovaPoshtaArea, NovaPoshtaRegion, NovaPoshtaSettlement


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


SETTLEMENT_FIELDS = {
    "settlement_type": "SettlementType",
    "description": "Description",
    "description_ru": "DescriptionRu",
    "description_translit": "DescriptionTranslit",
    "settlement_type_description": "SettlementTypeDescription",
    "settlement_type_description_ru": "SettlementTypeDescriptionRu",
    "settlement_type_description_translit": "SettlementTypeDescriptionTranslit",
    "latitude": "Latitude",
    "longitude": "Longitude",
    "index_1": "Index1",
    "index_2": "Index2",
    "index_coatsu_1": "IndexCOATSU1",
    **{f"delivery_{day}": f"Delivery{day}" for day in range(1, 8)},
    "special_cash_check": "SpecialCashCheck",
    "radius_home_delivery": "RadiusHomeDelivery",
    "radius_express_pick_up": "RadiusExpressPickUp",
    "radius_drop": "RadiusDrop",
    "warehouse": "Warehouse",
    "address_delivery_allowed": "AddressDeliveryAllowed",
}


@dataclass(frozen=True)
class SettlementSyncResult:
    processed: int


def _build_settlement(item, areas, regions, selected_area):
    try:
        ref = UUID(item["Ref"])
        area_ref = UUID(item["Area"])
        raw_region = item["Region"]
        region_ref = UUID(raw_region) if raw_region else None
        if region_ref and region_ref.int == 0:
            region_ref = None
        if area_ref not in areas:
            raise NovaPoshtaError("Область отсутствует в базе. Обновите справочник областей.")
        if selected_area is not None and area_ref != selected_area.ref:
            raise NovaPoshtaError("API вернул населённый пункт другой области.")
        if region_ref is not None and regions.get(region_ref) != area_ref:
            raise NovaPoshtaError("Район отсутствует или относится к другой области. Обновите справочник районов.")
        obj = NovaPoshtaSettlement(ref=ref, area_id=area_ref, region_id=region_ref)
        for name, api_name in SETTLEMENT_FIELDS.items():
            field = NovaPoshtaSettlement._meta.get_field(name)
            value = item[api_name]
            if field.null and value == "":
                value = None
            # Преобразование типов и проверка ограничений без запросов к БД.
            setattr(obj, name, field.clean(value, obj))
        return obj
    except (KeyError, TypeError, ValueError, AttributeError, ValidationError) as exc:
        raise NovaPoshtaError(
            f"Некорректные данные населённого пункта: Ref={item.get('Ref', '?') if isinstance(item, dict) else '?'}."
        ) from exc


def sync_settlements(area=None):
    """Постраничная загрузка и пакетная перезапись полей по Ref без удаления строк."""
    areas = set(NovaPoshtaArea.objects.values_list("ref", flat=True))
    if not areas:
        raise NovaPoshtaError("Сначала обновите справочник областей Новой почты.")
    regions = dict(NovaPoshtaRegion.objects.values_list("ref", "area_id"))
    objects = []
    seen = set()
    limit = 150
    for page in range(1, 1001):
        properties = {"Page": str(page), "Limit": str(limit)}
        if area is not None:
            properties["AreaRef"] = str(area.ref)
        data = _fetch_catalog("getSettlements", properties)
        for item in data:
            obj = _build_settlement(item, areas, regions, area)
            if obj.ref in seen:
                raise NovaPoshtaError("API вернул повторяющиеся населённые пункты. Обновление отменено.")
            seen.add(obj.ref)
            objects.append(obj)
        if len(data) < limit:
            break
    else:
        raise NovaPoshtaError("Превышен лимит страниц справочника населённых пунктов.")

    # Ни одной записи до полной загрузки и проверки всех страниц.
    with transaction.atomic():
        NovaPoshtaSettlement.objects.bulk_create(
            objects,
            batch_size=500,
            update_conflicts=True,
            unique_fields=["ref"],
            update_fields=["area", "region", *SETTLEMENT_FIELDS],
        )
    return SettlementSyncResult(processed=len(objects))
