import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import NovaPoshtaArea, NovaPoshtaRegion, NovaPoshtaSettlement
from .nova_poshta import NovaPoshtaError, SettlementSyncResult, sync_settlements


class SettlementDataMixin:
    def setUp(self):
        self.item = json.loads(
            (Path(__file__).parent / "testdata/nova_poshta_settlement.json").read_text()
        )
        self.area = NovaPoshtaArea.objects.create(ref=self.item["Area"], description="Полтавська")
        self.region = NovaPoshtaRegion.objects.create(
            ref=self.item["Region"], area=self.area, description="Полтавський", region_type="район"
        )
        self.area.refresh_from_db()
        self.region.refresh_from_db()


class SettlementSyncTests(SettlementDataMixin, TestCase):
    @patch("catalogs.nova_poshta._fetch_catalog")
    def test_types_and_upsert_preserve_references(self, fetch):
        fetch.return_value = [self.item]
        self.assertEqual(sync_settlements(self.area), SettlementSyncResult(1))
        obj = NovaPoshtaSettlement.objects.get()
        self.assertEqual(obj.latitude, Decimal("49.605372000000000"))
        self.assertEqual(obj.index_1, "38715")
        self.assertFalse(obj.delivery_2)
        self.assertTrue(obj.delivery_1)
        self.assertTrue(obj.special_cash_check)
        self.assertEqual(obj.area, self.area)
        self.assertEqual(obj.region, self.region)
        # Проверяем сохранение ссылок на строку при upsert через временную таблицу.
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute(
                'CREATE TABLE settlement_reference (ref char(32) REFERENCES catalogs_novaposhtasettlement(ref))'
            )
            cursor.execute('INSERT INTO settlement_reference VALUES (%s)', [obj.ref.hex])
        try:
            self.item["Description"] = "Новое название"
            self.item["Delivery1"] = "0"
            self.assertEqual(sync_settlements(self.area), SettlementSyncResult(1))
            obj.refresh_from_db()
            self.assertEqual(obj.description, "Новое название")
            self.assertFalse(obj.delivery_1)
            self.assertEqual(NovaPoshtaSettlement.objects.count(), 1)
        finally:
            with connection.cursor() as cursor:
                cursor.execute('DROP TABLE settlement_reference')

    @patch("catalogs.nova_poshta._fetch_catalog")
    def test_pagination_and_all_areas(self, fetch):
        first_page = []
        for index in range(150):
            item = {**self.item, "Ref": str(UUID(int=index + 1))}
            first_page.append(item)
        fetch.side_effect = [first_page, [self.item]]
        self.assertEqual(sync_settlements(), SettlementSyncResult(151))
        self.assertEqual(fetch.call_args_list[0].args, (
            "getSettlements", {"Page": "1", "Limit": "150"}
        ))
        self.assertEqual(fetch.call_args_list[1].args[1]["Page"], "2")
        fetch.side_effect = None
        fetch.return_value = [self.item]
        sync_settlements(self.area)
        self.assertEqual(fetch.call_args.args[1]["AreaRef"], str(self.area.ref))

    @patch("catalogs.nova_poshta._fetch_catalog")
    def test_error_on_later_page_does_not_save_first_page(self, fetch):
        fetch.side_effect = [
            [{**self.item, "Ref": str(UUID(int=n + 1))} for n in range(150)],
            NovaPoshtaError("Timeout"),
        ]
        with self.assertRaises(NovaPoshtaError):
            sync_settlements(self.area)
        self.assertFalse(NovaPoshtaSettlement.objects.exists())

    @patch("catalogs.nova_poshta._fetch_catalog")
    def test_invalid_data_does_not_overwrite_existing(self, fetch):
        fetch.return_value = [self.item]
        sync_settlements(self.area)
        for change in (
            {"Delivery2": "maybe"}, {"Latitude": "100"},
            {"Area": str(UUID(int=42))}, {"Region": str(UUID(int=42))},
            {"RadiusDrop": "-1"}, {"Ref": "invalid"},
        ):
            with self.subTest(change=change):
                fetch.return_value = [{**self.item, "Description": "Не сохранять", **change}]
                with self.assertRaises(NovaPoshtaError):
                    sync_settlements(self.area)
                self.assertEqual(NovaPoshtaSettlement.objects.get().description, self.item["Description"])

    @patch("catalogs.nova_poshta._fetch_catalog")
    def test_empty_region_and_empty_response(self, fetch):
        fetch.return_value = [{**self.item, "Region": "", "Latitude": "", "Index1": "00123", "Delivery2": "", "Warehouse": ""}]
        sync_settlements(self.area)
        obj = NovaPoshtaSettlement.objects.get()
        self.assertIsNone(obj.region)
        self.assertIsNone(obj.latitude)
        self.assertIsNone(obj.delivery_2)
        self.assertIsNone(obj.warehouse)
        self.assertEqual(obj.index_1, "00123")
        fetch.return_value = []
        self.assertEqual(sync_settlements(self.area), SettlementSyncResult(0))
        self.assertTrue(NovaPoshtaSettlement.objects.filter(ref=obj.ref).exists())

    @patch("catalogs.nova_poshta._fetch_catalog")
    def test_region_area_consistency(self, fetch):
        other = NovaPoshtaArea.objects.create(ref=UUID(int=42), description="Другая область")
        self.region.area = other
        self.region.save()
        fetch.return_value = [self.item]
        with self.assertRaises(NovaPoshtaError):
            sync_settlements(self.area)
        obj = NovaPoshtaSettlement(area=self.area, region=self.region)
        with self.assertRaises(ValidationError):
            obj.clean()


@override_settings(LANGUAGE_CODE="ru")
class SettlementAdminTests(SettlementDataMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user("settlement-staff", is_staff=True)
        self.user.user_permissions.set(Permission.objects.filter(
            content_type__app_label="catalogs",
            codename__in=["view_novaposhtasettlement", "add_novaposhtasettlement", "change_novaposhtasettlement"],
        ))
        self.client.force_login(self.user)
        self.url = reverse("admin:catalogs_novaposhtasettlement_update_settlements")

    @patch("catalogs.admin.sync_settlements", return_value=SettlementSyncResult(150))
    def test_dialog_selected_and_all(self, sync):
        self.assertContains(self.client.get(self.url), "Все области")
        sync.assert_not_called()
        self.client.post(self.url, {"_form_submitted": "on", "area": str(self.area.ref)})
        sync.assert_called_once_with(area=self.area)
        sync.reset_mock()
        response = self.client.post(self.url, {"_form_submitted": "on", "area": ""}, HTTP_HX_REQUEST="true")
        sync.assert_called_once_with(area=None)
        self.assertEqual(response.headers["HX-Redirect"], reverse("admin:catalogs_novaposhtasettlement_changelist"))

    @patch("catalogs.admin.sync_settlements")
    def test_invalid_area_permissions_and_csrf(self, sync):
        self.client.post(self.url, {"_form_submitted": "on", "area": "invalid"})
        sync.assert_not_called()
        from django.test import Client
        protected_client = Client(enforce_csrf_checks=True)
        protected_client.force_login(self.user)
        self.assertEqual(protected_client.post(self.url, {"_form_submitted": "on"}).status_code, 403)
        self.user.user_permissions.clear()
        self.assertEqual(self.client.post(self.url, {"_form_submitted": "on"}).status_code, 403)
        sync.assert_not_called()

    @patch("catalogs.admin.sync_settlements", side_effect=NovaPoshtaError("Ошибка загрузки"))
    def test_error_message(self, sync):
        response = self.client.post(self.url, {"_form_submitted": "on"}, follow=True)
        self.assertContains(response, "Ошибка загрузки")

    @patch("catalogs.nova_poshta._fetch_catalog")
    def test_changelist_and_unicode_search(self, fetch):
        fetch.return_value = [self.item]
        sync_settlements(self.area)
        url = reverse("admin:catalogs_novaposhtasettlement_changelist")
        for term in ("аба", "АБА", "абазов", "abazi"):
            response = self.client.get(url, {"q": term})
            self.assertContains(response, "Абазівка")
