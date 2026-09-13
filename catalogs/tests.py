import json
from io import BytesIO
from unittest.mock import patch
from urllib.error import URLError
from uuid import UUID

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import NovaPoshtaArea, NovaPoshtaRegion
from .nova_poshta import AreaSyncResult, NovaPoshtaError, sync_areas, sync_regions


class NovaPoshtaAreaTests(TestCase):
    ref = UUID('71508129-9b87-11de-822f-000c2965ae0e')

    def response(self, data):
        return BytesIO(json.dumps(data).encode())

    @patch('catalogs.nova_poshta.urlopen')
    def test_create_update_and_repeat(self, send):
        payload = {'success': True, 'data': [{'Ref': str(self.ref), 'Description': 'Вінницька'}]}
        for expected in (AreaSyncResult(1, 0, 0), AreaSyncResult(0, 0, 1)):
            send.return_value = self.response(payload)
            self.assertEqual(sync_areas(), expected)
        payload['data'][0]['Description'] = 'Новое название'
        send.return_value = self.response(payload)
        self.assertEqual(sync_areas(), AreaSyncResult(0, 1, 0))
        self.assertEqual(NovaPoshtaArea.objects.get().description, 'Новое название')
        request = send.call_args.args[0]
        self.assertEqual(json.loads(request.data)['calledMethod'], 'getSettlementAreas')
        self.assertEqual(send.call_args.kwargs['timeout'], 15)

    @patch('catalogs.nova_poshta.urlopen')
    def test_bad_responses_do_not_change_database(self, send):
        NovaPoshtaArea.objects.create(ref=self.ref, description='Исходное')
        for payload in (
            {'success': False}, {'success': True, 'data': []},
            {'success': True, 'data': [
                {'Ref': str(self.ref), 'Description': 'Измененное'},
                {'Ref': 'invalid', 'Description': 'Ошибка'},
            ]},
        ):
            with self.subTest(payload=payload):
                send.return_value = self.response(payload)
                with self.assertRaises(NovaPoshtaError):
                    sync_areas()
                self.assertEqual(NovaPoshtaArea.objects.get().description, 'Исходное')
        send.return_value = BytesIO(b'not json')
        with self.assertRaises(NovaPoshtaError):
            sync_areas()
        send.side_effect = URLError('offline')
        with self.assertRaises(NovaPoshtaError):
            sync_areas()


@override_settings(LANGUAGE_CODE="ru")
class NovaPoshtaAreaAdminTests(TestCase):
    def test_autocomplete_search_ignores_cyrillic_case(self):
        area = NovaPoshtaArea.objects.create(
            ref=UUID("dcaae303-4b33-11e4-ab6d-005056801329"),
            description="Харківська",
        )
        for term in ("ха", "Ха", "ХА", "харків", str(area.ref)):
            with self.subTest(term=term):
                response = self.client.get(reverse("admin:autocomplete"), {
                    "app_label": "catalogs", "model_name": "novaposhtaregion",
                    "field_name": "area", "term": term,
                })
                self.assertEqual(response.status_code, 200)
                self.assertIn(
                    {"id": str(area.ref), "text": "Харківська"},
                    response.json()["results"],
                )

    def setUp(self):
        self.user = get_user_model().objects.create_user('staff', is_staff=True)
        self.user.user_permissions.set(Permission.objects.filter(
            content_type__app_label='catalogs',
            codename__in=['view_novaposhtaarea', 'add_novaposhtaarea', 'change_novaposhtaarea'],
        ))
        self.client.force_login(self.user)
        self.url = reverse('admin:catalogs_novaposhtaarea_update_areas')

    @patch('catalogs.admin.sync_areas', return_value=AreaSyncResult(1, 2, 3))
    def test_action_get_only_opens_dialog_and_post_syncs(self, sync):
        response = self.client.get(reverse('admin:catalogs_novaposhtaarea_changelist'))
        self.assertContains(response, self.url)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        sync.assert_not_called()
        response = self.client.post(self.url, {'_form_submitted': 'on'}, HTTP_HX_REQUEST='true')
        self.assertEqual(response.headers['HX-Redirect'], reverse('admin:catalogs_novaposhtaarea_changelist'))
        sync.assert_called_once()

    @patch('catalogs.admin.sync_areas', side_effect=NovaPoshtaError('Сервис недоступен'))
    def test_error_is_shown(self, sync):
        response = self.client.post(self.url, {'_form_submitted': 'on'}, follow=True)
        self.assertContains(response, 'Сервис недоступен')

    @patch('catalogs.admin.sync_areas')
    def test_permission_is_enforced(self, sync):
        self.user.user_permissions.clear()
        self.assertEqual(self.client.post(self.url, {'_form_submitted': 'on'}).status_code, 403)
        sync.assert_not_called()


class NovaPoshtaRegionTests(TestCase):
    def setUp(self):
        self.area = NovaPoshtaArea.objects.create(
            ref=UUID("dcaad5a7-4b33-11e4-ab6d-005056801329"), description="Вінницька"
        )
        self.other = NovaPoshtaArea.objects.create(
            ref=UUID("dcaad3d6-4b33-11e4-ab6d-005056801329"), description="АРК"
        )
        self.ref = UUID("e4af6e10-4b33-11e4-ab6d-005056801329")
        self.data = {self.ref: {"description": "Барський", "region_type": "район"}}

    @patch("catalogs.nova_poshta.fetch_regions")
    def test_selected_area_and_repeat(self, fetch):
        fetch.return_value = self.data
        self.assertEqual(sync_regions(self.area), AreaSyncResult(1, 0, 0))
        fetch.assert_called_once_with(self.area.ref)
        self.assertEqual(NovaPoshtaRegion.objects.get().area_id, self.area.ref)
        self.assertEqual(sync_regions(self.area), AreaSyncResult(0, 0, 1))
        fetch.return_value = {self.ref: {"description": "Новое название", "region_type": "Новий тип"}}
        self.assertEqual(sync_regions(self.area), AreaSyncResult(0, 1, 0))
        region = NovaPoshtaRegion.objects.get()
        self.assertEqual((region.description, region.region_type), ("Новое название", "Новий тип"))

    @patch("catalogs.nova_poshta.fetch_regions")
    def test_all_areas_and_failure_without_partial_writes(self, fetch):
        fetch.side_effect = [self.data, NovaPoshtaError("Ошибка")]
        with self.assertRaises(NovaPoshtaError):
            sync_regions()
        self.assertFalse(NovaPoshtaRegion.objects.exists())
        fetch.reset_mock()
        fetch.side_effect = [self.data, {}]
        self.assertEqual(sync_regions(), AreaSyncResult(1, 0, 0))
        self.assertEqual({call.args[0] for call in fetch.call_args_list}, {self.area.ref, self.other.ref})

    @patch("catalogs.nova_poshta.urlopen")
    def test_api_payload_and_validation(self, send):
        from .nova_poshta import fetch_regions
        send.return_value = BytesIO(json.dumps({"success": True, "data": [
            {"Ref": str(self.ref), "Description": "Барський", "RegionType": "район"}
        ]}).encode())
        self.assertEqual(fetch_regions(self.area.ref), self.data)
        request = json.loads(send.call_args.args[0].data)
        self.assertEqual(request["calledMethod"], "getSettlementCountryRegion")
        self.assertEqual(request["methodProperties"], {"AreaRef": str(self.area.ref)})
        send.return_value = BytesIO(b'{"success":true,"data":[{}]}')
        with self.assertRaises(NovaPoshtaError):
            fetch_regions(self.area.ref)

    def test_empty_area_catalog(self):
        NovaPoshtaArea.objects.all().delete()
        with self.assertRaisesMessage(NovaPoshtaError, "Сначала обновите"):
            sync_regions()


@override_settings(LANGUAGE_CODE="ru")
class NovaPoshtaRegionAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("region-staff", is_staff=True)
        self.user.user_permissions.set(Permission.objects.filter(
            content_type__app_label="catalogs",
            codename__in=["view_novaposhtaregion", "add_novaposhtaregion", "change_novaposhtaregion"],
        ))
        self.client.force_login(self.user)
        self.area = NovaPoshtaArea.objects.create(
            ref=UUID("dcaad5a7-4b33-11e4-ab6d-005056801329"), description="Вінницька"
        )
        self.url = reverse("admin:catalogs_novaposhtaregion_update_regions")

    @patch("catalogs.admin.sync_regions", return_value=AreaSyncResult(1, 0, 0))
    def test_dialog_selected_and_all(self, sync):
        response = self.client.get(self.url)
        self.assertContains(response, "Все области")
        self.assertContains(response, "Вінницька")
        sync.assert_not_called()
        self.client.post(self.url, {"_form_submitted": "on", "area": str(self.area.ref)})
        sync.assert_called_once_with(area=self.area)
        sync.reset_mock()
        response = self.client.post(self.url, {"_form_submitted": "on", "area": ""}, HTTP_HX_REQUEST="true")
        sync.assert_called_once_with(area=None)
        self.assertEqual(response.headers["HX-Redirect"], reverse("admin:catalogs_novaposhtaregion_changelist"))

    @patch("catalogs.admin.sync_regions")
    def test_invalid_area_and_permission(self, sync):
        response = self.client.post(self.url, {"_form_submitted": "on", "area": "invalid"})
        self.assertEqual(response.status_code, 200)
        sync.assert_not_called()
        self.user.user_permissions.clear()
        self.assertEqual(self.client.post(self.url, {"_form_submitted": "on"}).status_code, 403)
        sync.assert_not_called()
