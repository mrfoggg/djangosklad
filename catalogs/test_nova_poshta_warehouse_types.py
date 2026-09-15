from unittest.mock import patch
from django.test import TestCase
from django.db.models.deletion import ProtectedError
from .models import NovaPoshtaWarehouse, NovaPoshtaWarehouseType
from .nova_poshta import sync_warehouse_types, NovaPoshtaError


class WarehouseTypeTests(TestCase):
    item = {"Ref": "841339c7-591a-42e2-8233-7a0a00f0ed6f", "Description": "Поштове(ий)", "DescriptionRu": "Почтовое отделение"}

    @patch("catalogs.nova_poshta._fetch_catalog")
    def test_sync_links_existing_warehouses(self, fetch):
        warehouse = NovaPoshtaWarehouse.objects.create(
            ref="e6627e75-de7e-11e9-b48a-005056b24375", number="1", description="Отделение",
            warehouse_type_ref=self.item["Ref"],
        )
        fetch.return_value = [self.item]
        self.assertEqual(sync_warehouse_types().created, 1)
        fetch.assert_called_once_with("getWarehouseTypes", {})
        warehouse.refresh_from_db()
        self.assertEqual(warehouse.warehouse_type.description, self.item["Description"])
        self.assertEqual(sync_warehouse_types().unchanged, 1)
        fetch.return_value = [{**self.item, "Description": "Новое название"}]
        self.assertEqual(sync_warehouse_types().updated, 1)
        with self.assertRaises(ProtectedError):
            warehouse.warehouse_type.delete()
        fetch.return_value = [{**self.item, "Ref": "9a68df70-0267-42a8-bb5c-37f427e36ee4"}]
        self.assertEqual(sync_warehouse_types().deactivated, 1)
        warehouse.warehouse_type.refresh_from_db()
        self.assertFalse(warehouse.warehouse_type.is_active)
        fetch.return_value = [self.item]
        sync_warehouse_types()
        warehouse.warehouse_type.refresh_from_db()
        self.assertTrue(warehouse.warehouse_type.is_active)

    @patch("catalogs.nova_poshta._fetch_catalog")
    def test_invalid_data_does_not_change_catalog(self, fetch):
        for data in ([], [self.item, self.item], [self.item, {"Ref": "bad"}]):
            fetch.return_value = data
            with self.assertRaises(NovaPoshtaError):
                sync_warehouse_types()
            self.assertFalse(NovaPoshtaWarehouseType.objects.exists())
