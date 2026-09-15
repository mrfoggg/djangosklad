import json
from dataclasses import dataclass
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import NovaPoshtaSettlementDetails, NovaPoshtaWarehouse

from .models import NovaPoshtaArea, NovaPoshtaRegion, NovaPoshtaSettlement, NovaPoshtaSettlementType


class NovaPoshtaError(Exception):
    """Ошибка загрузки справочника Новой почты."""


def sync_settlement_details(settlement):
    data = _fetch_catalog("searchSettlements", {
        "SettlementRef": str(settlement.ref), "Page": "1", "Limit": "20",
    })
    try:
        addresses = [address for group in data for address in group["Addresses"]]
        matches = [item for item in addresses if UUID(item["Ref"]) == settlement.ref]
        if len(matches) != 1:
            raise ValueError("Settlement not found or ambiguous")
        address = matches[0]
        city_ref = UUID(address["DeliveryCity"])
        cities = _fetch_catalog("getCities", {"Ref": str(city_ref), "Page": "1", "Limit": "20"})
        matches = [item for item in cities if UUID(item["Ref"]) == city_ref]
        if len(matches) != 1:
            raise ValueError("Delivery city not found or ambiguous")
        city = matches[0]
        values = {
            "address_delivery_allowed": address["AddressDeliveryAllowed"],
            "streets_availability": address["StreetsAvailability"],
            "delivery_city_ref": city_ref,
            "description": city["Description"],
            "settlement_type": city["SettlementType"],
            "settlement_type_description": city["SettlementTypeDescription"],
            "prevent_entry_new_streets_user": city["PreventEntryNewStreetsUser"],
            "city_id": city["CityID"],
            "special_cash_check": city["SpecialCashCheck"],
            "area_description": city["AreaDescription"],
            **{f"delivery_{day}": city[f"Delivery{day}"] for day in range(1, 8)},
        }
        obj = NovaPoshtaSettlementDetails(settlement=settlement)
        for name, value in values.items():
            values[name] = obj._meta.get_field(name).clean(value, obj)
    except (KeyError, TypeError, ValueError, AttributeError, ValidationError) as exc:
        raise NovaPoshtaError("Не удалось получить корректные допданные населённого пункта и города доставки.") from exc
    with transaction.atomic():
        details, _created = NovaPoshtaSettlementDetails.objects.update_or_create(
            settlement=settlement, defaults=values,
        )
    return details


@dataclass(frozen=True)
class AreaSyncResult:
    created: int
    updated: int
    unchanged: int
    deactivated: int = 0


def _fetch_catalog(method, properties, model="AddressGeneral"):
    request = Request(
        "https://api.novaposhta.ua/v2.0/json/",
        data=json.dumps({
            "modelName": model,
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

    # urllib follows HTTP 303 after POST with a GET to the cached file.
    # Cached warehouse files may contain the data array without an API envelope.
    if method == "getWarehouses" and isinstance(payload, list):
        return payload
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
            elif area.description != description or not area.is_active:
                area.is_active = True
                area.description = description
                area.save(update_fields=["description", "is_active", "updated"])
                updated += 1
            else:
                unchanged += 1
        deactivated = _deactivate_missing(NovaPoshtaArea.objects.all(), areas)
    return AreaSyncResult(created, updated, unchanged, deactivated)


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
            regions[ref] = {**values, "area_id": current_area.ref, "is_active": True}

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
                region.save(update_fields=[*changed, "updated"])
                updated += 1
            else:
                unchanged += 1
        scope = NovaPoshtaRegion.objects.all()
        if area is not None:
            scope = scope.filter(area=area)
        deactivated = _deactivate_missing(scope, regions)
    return AreaSyncResult(created, updated, unchanged, deactivated)


SETTLEMENT_FIELDS = {
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
    deactivated: int = 0


def _build_settlement(item, areas, regions, selected_area, settlement_types):
    try:
        ref = UUID(item["Ref"])
        area_ref = UUID(item["Area"])
        type_ref = UUID(item["SettlementType"])
        if type_ref not in settlement_types:
            raise NovaPoshtaError("Тип населённого пункта отсутствует. Обновите справочник типов населённых пунктов.")
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
        obj = NovaPoshtaSettlement(ref=ref, area_id=area_ref, region_id=region_ref, settlement_type_id=type_ref)
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
    settlement_types = set(NovaPoshtaSettlementType.objects.values_list("ref", flat=True))
    objects = []
    seen = set()
    limit = 150
    for page in range(1, 1001):
        properties = {"Page": str(page), "Limit": str(limit)}
        if area is not None:
            properties["AreaRef"] = str(area.ref)
        data = _fetch_catalog("getSettlements", properties)
        for item in data:
            obj = _build_settlement(item, areas, regions, area, settlement_types)
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
            update_fields=["area", "region", "settlement_type", *SETTLEMENT_FIELDS, "is_active", "updated"],
        )
        scope = NovaPoshtaSettlement.objects.all()
        if area is not None:
            scope = scope.filter(area=area)
        deactivated = _deactivate_missing(scope, seen)
    return SettlementSyncResult(processed=len(objects), deactivated=deactivated)


def sync_settlement_types():
    data = _fetch_catalog("getSettlementTypes", {})
    if not data:
        raise NovaPoshtaError("Новая почта вернула пустой справочник типов населённых пунктов.")
    objects = {}
    try:
        for item in data:
            obj = NovaPoshtaSettlementType(
                ref=UUID(item["Ref"]), description=item["Description"], code=item["Code"]
            )
            obj.clean_fields()
            if obj.ref in objects:
                raise ValueError("Duplicate ref")
            objects[obj.ref] = obj
    except (KeyError, TypeError, ValueError, AttributeError, ValidationError) as exc:
        raise NovaPoshtaError("Некорректные данные типа населённого пункта в ответе Новой почты.") from exc
    created = updated = unchanged = 0
    with transaction.atomic():
        for ref, obj in objects.items():
            current, is_new = NovaPoshtaSettlementType.objects.get_or_create(
                ref=ref, defaults={"description": obj.description, "code": obj.code}
            )
            if is_new:
                created += 1
            elif (current.description, current.code) != (obj.description, obj.code) or not current.is_active:
                current.is_active = True
                current.description, current.code = obj.description, obj.code
                current.save(update_fields=["description", "code", "is_active", "updated"])
                updated += 1
            else:
                unchanged += 1
        deactivated = _deactivate_missing(NovaPoshtaSettlementType.objects.all(), objects)
    return AreaSyncResult(created, updated, unchanged, deactivated)


def _deactivate_missing(queryset, received_refs):
    """Вызывается внутри транзакции после полной успешной загрузки."""
    received_refs = set(received_refs)
    missing = [ref for ref in queryset.filter(is_active=True).values_list("ref", flat=True) if ref not in received_refs]
    count = 0
    now = timezone.now()
    for offset in range(0, len(missing), 500):
        count += queryset.filter(ref__in=missing[offset:offset + 500], is_active=True).update(
            is_active=False, updated=now
        )
    return count


WAREHOUSE_FIELDS = {
    "site_key": "SiteKey",
    "short_address_ru": "ShortAddressRu",
    "city_description": "CityDescription",
    "city_description_ru": "CityDescriptionRu",
    "settlement_area_description": "SettlementAreaDescription",
    "settlement_regions_description": "SettlementRegionsDescription",
    "settlement_type_description": "SettlementTypeDescription",
    "settlement_type_description_ru": "SettlementTypeDescriptionRu",
    "district_code": "DistrictCode",
    "status_date": "WarehouseStatusDate",
    "direct": "Direct",
    "region_city": "RegionCity",
    "post_machine_type": "PostMachineType",
    "postomat_for": "PostomatFor",
    "warehouse_index": "WarehouseIndex",
    "beacon_code": "BeaconCode",
    "location": "Location",
    "post_finance": "PostFinance",
    "bicycle_parking": "BicycleParking",
    "payment_access": "PaymentAccess",
    "pos_terminal": "POSTerminal",
    "international_shipping": "InternationalShipping",
    "warehouse_illusha": "WarehouseIllusha",
    "warehouse_for_agent": "WarehouseForAgent",
    "generator_enabled": "GeneratorEnabled",
    "work_in_mobile_awis": "WorkInMobileAwis",
    "deny_to_select": "DenyToSelect",
    "can_get_money_transfer": "CanGetMoneyTransfer",
    "has_mirror": "HasMirror",
    "has_fitting_room": "HasFittingRoom",
    "only_receiving_parcel": "OnlyReceivingParcel",
    "self_service_workplaces_count": "SelfServiceWorkplacesCount",
    "max_declared_cost": "MaxDeclaredCost",

    "number": "Number", "description": "Description", "description_ru": "DescriptionRu",
    "short_address": "ShortAddress", "phone": "Phone", "category": "CategoryOfWarehouse",
    "status": "WarehouseStatus", "postal_code": "PostalCodeUA",
    "settlement_description": "SettlementDescription", "latitude": "Latitude", "longitude": "Longitude",
    "total_max_weight": "TotalMaxWeightAllowed", "place_max_weight": "PlaceMaxWeightAllowed",
    "schedule": "Schedule", "reception": "Reception", "delivery": "Delivery",
    "sending_dimensions": "SendingLimitationsOnDimensions", "receiving_dimensions": "ReceivingLimitationsOnDimensions",
}


def _build_warehouse(item, settlements, selected_settlement):
    try:
        def optional_ref(value):
            ref = UUID(value) if value else None
            return ref if ref and ref.int else None

        settlement_ref = optional_ref(item.get("SettlementRef"))
        if selected_settlement is not None and settlement_ref != selected_settlement.ref:
            raise ValueError("Unexpected settlement")
        obj = NovaPoshtaWarehouse(
            ref=UUID(item["Ref"]), settlement_ref=settlement_ref,
            settlement_id=settlement_ref if settlement_ref in settlements else None,
            city_ref=optional_ref(item.get("CityRef")),
            warehouse_type_ref=UUID(item["TypeOfWarehouse"]), raw_data=item,
        )
        for name, api_name in WAREHOUSE_FIELDS.items():
            field = obj._meta.get_field(name)
            if name in ("number", "description"):
                value = item[api_name]
            else:
                value = item.get(api_name, field.get_default())
            if field.null and value in ("", None):
                value = None
            elif value is None and field.blank:
                value = field.get_default()
            if name in ("schedule", "reception", "delivery", "sending_dimensions", "receiving_dimensions") and not isinstance(value, dict):
                raise ValueError("Expected JSON object")
            setattr(obj, name, field.clean(value, obj))
        return obj
    except (KeyError, TypeError, ValueError, AttributeError, ValidationError) as exc:
        raise NovaPoshtaError("Некорректные данные отделения Новой почты. Обновление отменено.") from exc


def sync_warehouses(settlement=None):
    """Загрузить до пустой страницы, проверить весь ответ и атомарно обновить справочник."""
    settlements = set(NovaPoshtaSettlement.objects.values_list("ref", flat=True))
    objects, seen = [], set()
    for page in range(1, 1001):
        properties = {"Page": str(page), "Limit": "500"}
        if settlement is not None:
            properties["SettlementRef"] = str(settlement.ref)
        data = _fetch_catalog("getWarehouses", properties, model="Address")
        if not data:
            break
        for item in data:
            obj = _build_warehouse(item, settlements, settlement)
            if obj.ref in seen:
                raise NovaPoshtaError("API повторяет отделения между страницами. Обновление отменено.")
            seen.add(obj.ref)
            objects.append(obj)
    else:
        raise NovaPoshtaError("Превышен лимит страниц справочника отделений.")
    if settlement is None and not objects:
        raise NovaPoshtaError("Новая почта вернула пустой общий справочник отделений. Обновление отменено.")
    with transaction.atomic():
        NovaPoshtaWarehouse.objects.bulk_create(
            objects, batch_size=500, update_conflicts=True, unique_fields=["ref"],
            update_fields=["settlement", "settlement_ref", "city_ref", "warehouse_type_ref", *WAREHOUSE_FIELDS, "raw_data", "is_active", "updated"],
        )
        scope = NovaPoshtaWarehouse.objects.all()
        if settlement is not None:
            scope = scope.filter(settlement_ref=settlement.ref)
        deactivated = _deactivate_missing(scope, seen)
    return SettlementSyncResult(len(objects), deactivated)
