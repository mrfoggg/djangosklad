from django.db import migrations


FIELDS = {'site_key': 'SiteKey', 'short_address_ru': 'ShortAddressRu', 'city_description': 'CityDescription', 'city_description_ru': 'CityDescriptionRu', 'settlement_area_description': 'SettlementAreaDescription', 'settlement_regions_description': 'SettlementRegionsDescription', 'settlement_type_description': 'SettlementTypeDescription', 'settlement_type_description_ru': 'SettlementTypeDescriptionRu', 'district_code': 'DistrictCode', 'status_date': 'WarehouseStatusDate', 'direct': 'Direct', 'region_city': 'RegionCity', 'post_machine_type': 'PostMachineType', 'postomat_for': 'PostomatFor', 'warehouse_index': 'WarehouseIndex', 'beacon_code': 'BeaconCode', 'location': 'Location', 'post_finance': 'PostFinance', 'bicycle_parking': 'BicycleParking', 'payment_access': 'PaymentAccess', 'pos_terminal': 'POSTerminal', 'international_shipping': 'InternationalShipping', 'warehouse_illusha': 'WarehouseIllusha', 'warehouse_for_agent': 'WarehouseForAgent', 'generator_enabled': 'GeneratorEnabled', 'work_in_mobile_awis': 'WorkInMobileAwis', 'deny_to_select': 'DenyToSelect', 'can_get_money_transfer': 'CanGetMoneyTransfer', 'has_mirror': 'HasMirror', 'has_fitting_room': 'HasFittingRoom', 'only_receiving_parcel': 'OnlyReceivingParcel', 'self_service_workplaces_count': 'SelfServiceWorkplacesCount', 'max_declared_cost': 'MaxDeclaredCost'}


def backfill(apps, schema_editor):
    Warehouse = apps.get_model("catalogs", "NovaPoshtaWarehouse")
    manager = Warehouse.objects.using(schema_editor.connection.alias)
    batch = []
    for obj in manager.all().iterator(chunk_size=500):
        for name, api_name in FIELDS.items():
            field = Warehouse._meta.get_field(name)
            value = obj.raw_data.get(api_name, field.get_default())
            if field.null and value in ("", None):
                value = None
            elif value is None and field.blank:
                value = field.get_default()
            setattr(obj, name, field.clean(value, obj))
        batch.append(obj)
        if len(batch) == 500:
            manager.bulk_update(batch, list(FIELDS), batch_size=100)
            batch = []
    if batch:
        manager.bulk_update(batch, list(FIELDS), batch_size=100)


class Migration(migrations.Migration):
    dependencies = [("catalogs", "0044_novaposhtawarehouse_beacon_code_and_more")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
