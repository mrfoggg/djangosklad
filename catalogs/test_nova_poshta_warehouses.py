import io
import json
from pathlib import Path
from unittest.mock import patch
from urllib.request import HTTPRedirectHandler, Request
from uuid import UUID

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import NovaPoshtaSettlement, NovaPoshtaWarehouse
from .nova_poshta import NovaPoshtaError, _fetch_catalog, sync_settlements, sync_warehouses
from .test_nova_poshta_settlements import SettlementDataMixin


@override_settings(LANGUAGE_CODE="ru")
class WarehouseTests(SettlementDataMixin, TestCase):
    def setUp(self):
        super().setUp()
        with patch("catalogs.nova_poshta._fetch_catalog", return_value=[self.item]):
            sync_settlements(self.area)
        self.settlement = NovaPoshtaSettlement.objects.get()
        self.warehouse = json.loads((Path(__file__).parent / "testdata/nova_poshta_warehouse.json").read_text())
        self.warehouse["SettlementRef"] = str(self.settlement.ref)

    @patch("catalogs.nova_poshta._fetch_catalog")
    def test_pagination_upsert_and_scope(self, fetch):
        fetch.side_effect = [[self.warehouse], []]
        self.assertEqual(sync_warehouses(self.settlement).processed, 1)
        self.assertEqual(fetch.call_count, 2)  # A short page is not the stopping condition.
        self.assertEqual(fetch.call_args_list[1].kwargs, {"model": "Address"})
        self.assertEqual(fetch.call_args_list[1].args[1], {"Page": "2", "Limit": "500", "SettlementRef": str(self.settlement.ref)})
        obj = NovaPoshtaWarehouse.objects.get()
        self.assertEqual(obj.settlement, self.settlement)
        self.assertEqual(obj.raw_data, self.warehouse)
        self.assertEqual(obj.schedule["Monday"], "09:00-17:00")
        created = obj.created
        self.warehouse["Description"] = "Новое название"
        fetch.side_effect = [[self.warehouse], []]
        sync_warehouses(self.settlement)
        obj.refresh_from_db()
        self.assertEqual(obj.description, "Новое название")
        self.assertEqual(obj.created, created)
        fetch.side_effect = [[]]
        self.assertEqual(sync_warehouses(self.settlement).deactivated, 1)
        obj.refresh_from_db()
        self.assertFalse(obj.is_active)
        fetch.side_effect = [[self.warehouse], []]
        sync_warehouses(self.settlement)
        obj.refresh_from_db()
        self.assertTrue(obj.is_active)

    @patch("catalogs.nova_poshta._fetch_catalog")
    def test_expanded_fields_and_backfill(self, fetch):
        from importlib import import_module
        from types import SimpleNamespace
        from django.apps import apps
        from django.db import connection
        from decimal import Decimal

        self.warehouse.update(SiteKey="20405", GeneratorEnabled="1", MaxDeclaredCost="29000", Location=None)
        fetch.side_effect = [[self.warehouse], []]
        sync_warehouses(self.settlement)
        obj = NovaPoshtaWarehouse.objects.get()
        self.assertEqual(obj.site_key, "20405")
        self.assertEqual(obj.location, "")
        self.assertTrue(obj.generator_enabled)
        self.assertFalse(obj.pos_terminal)
        self.assertEqual(obj.max_declared_cost, Decimal("29000"))
        self.assertEqual(obj.self_service_workplaces_count, 0)
        self.assertEqual(obj.short_address_ru, self.warehouse["ShortAddressRu"])
        updated = obj.updated
        NovaPoshtaWarehouse.objects.filter(pk=obj.pk).update(site_key="", generator_enabled=None)
        migration = import_module("catalogs.migrations.0045_backfill_warehouse_fields")
        migration.backfill(apps, SimpleNamespace(connection=connection))
        obj.refresh_from_db()
        self.assertEqual(obj.site_key, "20405")
        self.assertEqual(obj.location, "")
        self.assertTrue(obj.generator_enabled)
        self.assertEqual(obj.updated, updated)

    @patch("catalogs.nova_poshta._fetch_catalog")
    def test_failure_is_atomic(self, fetch):
        for replies in (
            [[self.warehouse], NovaPoshtaError("Timeout")],
            [[self.warehouse], [self.warehouse]],
            [[{**self.warehouse, "Ref": "bad"}]],
            [[{**self.warehouse, "SettlementRef": str(UUID(int=1))}]],
        ):
            fetch.side_effect = replies
            with self.assertRaises(NovaPoshtaError):
                sync_warehouses(self.settlement)
            self.assertFalse(NovaPoshtaWarehouse.objects.exists())

    @patch("catalogs.nova_poshta._fetch_catalog")
    def test_unknown_settlement_preserved_and_linked_on_next_sync(self, fetch):
        self.warehouse["SettlementRef"] = str(UUID(int=1))
        fetch.side_effect = [[self.warehouse], []]
        sync_warehouses()
        obj = NovaPoshtaWarehouse.objects.get()
        self.assertIsNone(obj.settlement)
        self.assertEqual(obj.settlement_ref, UUID(int=1))
        # Once a matching settlement exists the next sync resolves the FK.
        self.warehouse["SettlementRef"] = str(self.settlement.ref)
        fetch.side_effect = [[self.warehouse], []]
        sync_warehouses()
        obj.refresh_from_db()
        self.assertEqual(obj.settlement, self.settlement)
        fetch.side_effect = [[]]
        with self.assertRaises(NovaPoshtaError):
            sync_warehouses()
        obj.refresh_from_db()
        self.assertTrue(obj.is_active)

    @patch("catalogs.nova_poshta.urlopen")
    def test_direct_and_cached_responses(self, urlopen):
        for payload in ({"success": True, "data": [self.warehouse]}, [self.warehouse]):
            urlopen.return_value = io.BytesIO(json.dumps(payload).encode())
            self.assertEqual(_fetch_catalog("getWarehouses", {"Page": "1"}, model="Address"), [self.warehouse])
            self.assertEqual(json.loads(urlopen.call_args.args[0].data)["modelName"], "Address")
        urlopen.return_value = io.BytesIO(b'{"success": false}')
        with self.assertRaises(NovaPoshtaError):
            _fetch_catalog("getWarehouses", {}, model="Address")

    def test_303_converts_post_to_get(self):
        request = Request("https://api.novaposhta.ua/v2.0/json/", data=b"{}", method="POST")
        redirected = HTTPRedirectHandler().redirect_request(request, None, 303, "See Other", {}, "https://api.novaposhta.ua/cache/data.json")
        self.assertEqual(redirected.get_method(), "GET")
        self.assertIsNone(redirected.data)

    @patch("catalogs.nova_poshta._fetch_catalog")
    def test_admin_inline_and_update_action(self, fetch):
        self.client.force_login(get_user_model().objects.create_user("warehouse-admin", is_staff=True, is_superuser=True))
        fetch.side_effect = [[self.warehouse], []]
        sync_warehouses(self.settlement)
        card = reverse("admin:catalogs_novaposhtasettlement_change", args=[self.settlement.pk])
        response = self.client.get(card)
        self.assertContains(response, self.warehouse["Description"])
        self.assertContains(response, "Обновить отделения")
        warehouse_card = reverse("admin:catalogs_novaposhtawarehouse_change", args=[self.warehouse["Ref"]])
        self.assertEqual(self.client.get(warehouse_card).status_code, 200)
        action = reverse("admin:catalogs_novaposhtasettlement_update_warehouses", args=[self.settlement.pk])
        fetch.reset_mock()
        self.assertEqual(self.client.get(action).status_code, 200)
        fetch.assert_not_called()
        fetch.side_effect = [[self.warehouse], []]
        self.assertEqual(self.client.post(action, {"_form_submitted": "on"}).status_code, 302)
