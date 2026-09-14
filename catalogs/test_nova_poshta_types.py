from unittest.mock import patch
from uuid import UUID

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import NovaPoshtaSettlementType
from .nova_poshta import AreaSyncResult, NovaPoshtaError, sync_settlement_types


class SettlementTypeTests(TestCase):
    @patch('catalogs.nova_poshta._fetch_catalog')
    def test_sync_and_invalid_response(self, fetch):
        ref = UUID('563ced13-f210-11e3-8c4a-0050568002cf')
        item = {'Ref': str(ref), 'Description': 'село', 'Code': 'с.'}
        fetch.return_value = [item]
        self.assertEqual(sync_settlement_types(), AreaSyncResult(1, 0, 0))
        fetch.assert_called_with('getSettlementTypes', {})
        self.assertEqual(sync_settlement_types(), AreaSyncResult(0, 0, 1))
        item['Code'] = 'с'
        self.assertEqual(sync_settlement_types(), AreaSyncResult(0, 1, 0))
        self.assertEqual(NovaPoshtaSettlementType.objects.get().code, 'с')
        for response in ([], [item, {'Ref': 'invalid'}], [item, item]):
            fetch.return_value = response
            with self.assertRaises(NovaPoshtaError):
                sync_settlement_types()
            self.assertEqual(NovaPoshtaSettlementType.objects.count(), 1)

    @override_settings(LANGUAGE_CODE='ru')
    @patch('catalogs.admin.sync_settlement_types', return_value=AreaSyncResult(4, 0, 0))
    def test_admin_action(self, sync):
        user = get_user_model().objects.create_user('type-admin', is_staff=True, is_superuser=True)
        self.client.force_login(user)
        url = reverse('admin:catalogs_novaposhtasettlementtype_update_settlement_types')
        self.assertEqual(self.client.get(url).status_code, 200)
        sync.assert_not_called()
        response = self.client.post(url, {'_form_submitted': 'on'}, HTTP_HX_REQUEST='true')
        self.assertEqual(response.headers['HX-Redirect'], reverse('admin:catalogs_novaposhtasettlementtype_changelist'))
        sync.assert_called_once()
        user.is_superuser = False
        user.save()
        self.assertEqual(self.client.post(url, {'_form_submitted': 'on'}).status_code, 403)
