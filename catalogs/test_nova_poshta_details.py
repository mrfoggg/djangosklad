from datetime import timedelta
from unittest.mock import patch
from uuid import UUID

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import NovaPoshtaSettlement, NovaPoshtaSettlementDetails
from .nova_poshta import sync_settlements, sync_settlement_details, NovaPoshtaError
from .test_nova_poshta_settlements import SettlementDataMixin


@override_settings(LANGUAGE_CODE='ru')
class SettlementDetailsTests(SettlementDataMixin, TestCase):
    def setUp(self):
        super().setUp()
        with patch('catalogs.nova_poshta._fetch_catalog', return_value=[self.item]):
            sync_settlements(self.area)
        self.settlement = NovaPoshtaSettlement.objects.get()
        self.city_ref = '8d5a980d-391c-11dd-90d9-001a92567626'
        self.search = [{'TotalCount': 1, 'Addresses': [{
            'Ref': str(self.settlement.ref), 'DeliveryCity': self.city_ref,
            'AddressDeliveryAllowed': True, 'StreetsAvailability': False,
        }]}]
        self.city = [{
            'Ref': self.city_ref, 'Description': 'Київ',
            'SettlementType': self.item['SettlementType'],
            'SettlementTypeDescription': 'місто', 'PreventEntryNewStreetsUser': '0',
            'CityID': '4', 'SpecialCashCheck': 1, 'AreaDescription': 'Київська',
            **{f'Delivery{day}': '1' for day in range(1, 8)},
        }]

    @patch('catalogs.nova_poshta._fetch_catalog')
    def test_sequential_requests_and_timestamps(self, fetch):
        fetch.side_effect = [self.search, self.city]
        obj = sync_settlement_details(self.settlement)
        self.assertEqual(obj.delivery_city_ref, UUID(self.city_ref))
        self.assertTrue(obj.address_delivery_allowed)
        self.assertFalse(obj.streets_availability)
        self.assertFalse(obj.prevent_entry_new_streets_user)
        self.assertTrue(obj.delivery_7)
        self.assertEqual(fetch.call_args_list[0].args, ('searchSettlements', {
            'SettlementRef': str(self.settlement.ref), 'Page': '1', 'Limit': '20',
        }))
        self.assertEqual(fetch.call_args_list[1].args, ('getCities', {
            'Ref': self.city_ref, 'Page': '1', 'Limit': '20',
        }))
        created = obj.created
        later = timezone.now() + timedelta(days=1)
        fetch.side_effect = [self.search, self.city]
        with patch('django.utils.timezone.now', return_value=later):
            sync_settlement_details(self.settlement)
        obj.refresh_from_db()
        self.assertEqual(obj.created, created)
        self.assertEqual(obj.updated, later)
        self.assertEqual(NovaPoshtaSettlementDetails.objects.count(), 1)
        # Обычное обновление не перезаписывает допданные.
        fetch.side_effect = [[self.item]]
        sync_settlements(self.area)
        obj.refresh_from_db()
        self.assertEqual(obj.updated, later)

    @patch('catalogs.nova_poshta._fetch_catalog')
    def test_error_preserves_previous_details(self, fetch):
        fetch.side_effect = [self.search, self.city]
        obj = sync_settlement_details(self.settlement)
        original_updated = obj.updated
        for reply in (NovaPoshtaError('Timeout'), [], [{**self.city[0], 'Ref': str(UUID(int=1))}], [{**self.city[0], 'Delivery1': 'invalid'}]):
            with self.subTest(reply=reply):
                fetch.side_effect = [self.search, reply]
                with self.assertRaises(NovaPoshtaError):
                    sync_settlement_details(self.settlement)
                obj.refresh_from_db()
                self.assertEqual(obj.updated, original_updated)
        fetch.side_effect = [[{'Addresses': [{**self.search[0]['Addresses'][0], 'Ref': str(UUID(int=1))}]}]]
        with self.assertRaises(NovaPoshtaError):
            sync_settlement_details(self.settlement)

    @patch('catalogs.nova_poshta._fetch_catalog')
    def test_readonly_card_action_and_inline(self, fetch):
        user = get_user_model().objects.create_user('details-admin', is_staff=True, is_superuser=True)
        self.client.force_login(user)
        url = reverse('admin:catalogs_novaposhtasettlement_update_details', args=[self.settlement.pk])
        card = reverse('admin:catalogs_novaposhtasettlement_change', args=[self.settlement.pk])
        response = self.client.get(card)
        self.assertContains(response, 'Обновить допданные')
        self.assertEqual(self.client.get(url).status_code, 200)
        fetch.assert_not_called()
        fetch.side_effect = [self.search, self.city]
        response = self.client.post(url, {'_form_submitted': 'on'}, HTTP_HX_REQUEST='true')
        self.assertEqual(response.headers['HX-Redirect'], card)
        response = self.client.get(card)
        self.assertContains(response, 'Київська')
        inline = response.context['inline_admin_formsets'][0]
        self.assertFalse(inline.has_add_permission)
        self.assertFalse(inline.has_change_permission)
        self.assertFalse(inline.has_delete_permission)
        self.assertIn('created', inline.readonly_fields)
        self.assertIn('updated', inline.readonly_fields)
        user.is_superuser = False
        user.save()
        self.assertEqual(self.client.post(url, {'_form_submitted': 'on'}).status_code, 403)
