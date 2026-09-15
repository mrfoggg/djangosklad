from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import NovaPoshtaArea, NovaPoshtaRegion, NovaPoshtaSettlement, NovaPoshtaSettlementType
from .nova_poshta import sync_areas, sync_regions, sync_settlement_types, sync_settlements
from .test_nova_poshta_settlements import SettlementDataMixin


@override_settings(LANGUAGE_CODE='ru')
class NovaPoshtaReadonlyTests(SettlementDataMixin, TestCase):
    def setUp(self):
        super().setUp()
        with patch('catalogs.nova_poshta._fetch_catalog', return_value=[self.item]):
            sync_settlements(self.area)

    def test_readonly_admin_for_all_catalogs(self):
        user = get_user_model().objects.create_user('readonly-admin', is_staff=True, is_superuser=True)
        self.client.force_login(user)
        for model in (NovaPoshtaArea, NovaPoshtaRegion, NovaPoshtaSettlementType, NovaPoshtaSettlement):
            with self.subTest(model=model):
                obj = model.objects.first()
                prefix = f'admin:catalogs_{model._meta.model_name}'
                url = reverse(prefix + '_change', args=[obj.pk])
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Создан')
                self.assertContains(response, 'Изменен')
                self.assertNotContains(response, 'name="_save"')
                self.assertFalse(response.context['has_change_permission'])
                self.assertFalse(response.context['has_delete_permission'])
                self.assertEqual(self.client.post(url, {'description': 'Изменить'}).status_code, 403)
                self.assertEqual(self.client.get(reverse(prefix + '_add')).status_code, 403)
                obj.refresh_from_db()
                self.assertIsNotNone(obj.created)
                self.assertIsNotNone(obj.updated)

    def test_sync_timestamps_and_creation_preserved(self):
        future = timezone.now() + timedelta(days=1)
        settlement = NovaPoshtaSettlement.objects.get()
        original_created = settlement.created
        with patch('catalogs.nova_poshta._fetch_catalog', return_value=[self.item]), patch('django.utils.timezone.now', return_value=future):
            sync_settlements(self.area)
        settlement.refresh_from_db()
        self.assertEqual(settlement.created, original_created)
        self.assertEqual(settlement.updated, future)

        operations = (
            (self.area, 'fetch_areas', {self.area.ref: 'Новое название'}, sync_areas, []),
            (self.region, 'fetch_regions', {self.region.ref: {'description': 'Новый район', 'region_type': 'район'}}, sync_regions, [self.area]),
            (NovaPoshtaSettlementType.objects.get(), '_fetch_catalog', [{'Ref': self.item['SettlementType'], 'Description': 'Новое имя типа', 'Code': 'с.'}], sync_settlement_types, []),
        )
        for obj, loader, data, sync, args in operations:
            original_created = obj.created
            with patch('catalogs.nova_poshta.' + loader, return_value=data), patch('django.utils.timezone.now', return_value=future):
                sync(*args)
            obj.refresh_from_db()
            self.assertEqual(obj.created, original_created)
            self.assertEqual(obj.updated, future)
            with patch('catalogs.nova_poshta.' + loader, return_value=data), patch('django.utils.timezone.now', return_value=future + timedelta(days=1)):
                sync(*args)
            obj.refresh_from_db()
            self.assertEqual(obj.updated, future)
