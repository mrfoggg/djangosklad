from unittest.mock import patch
from uuid import UUID

from django.test import TestCase

from .models import NovaPoshtaArea, NovaPoshtaRegion, NovaPoshtaSettlement
from .nova_poshta import sync_areas, sync_regions, sync_settlements, sync_settlement_types, NovaPoshtaError
from .test_nova_poshta_settlements import SettlementDataMixin


class ActiveTests(SettlementDataMixin, TestCase):
    def test_area_and_type_reactivation(self):
        other = NovaPoshtaArea.objects.create(ref=UUID(int=1), description='Другая')
        with patch('catalogs.nova_poshta.fetch_areas', return_value={self.area.ref: self.area.description}):
            self.assertEqual(sync_areas().deactivated, 1)
        other.refresh_from_db()
        timestamp = other.updated
        self.assertFalse(other.is_active)
        with patch('catalogs.nova_poshta.fetch_areas', return_value={self.area.ref: self.area.description, other.ref: other.description}):
            sync_areas()
        other.refresh_from_db()
        self.assertTrue(other.is_active)
        self.assertGreater(other.updated, timestamp)
        from .models import NovaPoshtaSettlementType
        obj = NovaPoshtaSettlementType.objects.get()
        obj.is_active = False
        obj.save()
        with patch('catalogs.nova_poshta._fetch_catalog', return_value=[{'Ref': str(obj.ref), 'Description': obj.description, 'Code': obj.code}]):
            sync_settlement_types()
        obj.refresh_from_db()
        self.assertTrue(obj.is_active)

    def test_region_scope_and_reactivation(self):
        other_area = NovaPoshtaArea.objects.create(ref=UUID(int=1), description='Другая')
        other = NovaPoshtaRegion.objects.create(ref=UUID(int=2), area=other_area, description='Другой', region_type='район')
        with patch('catalogs.nova_poshta.fetch_regions', return_value={}):
            self.assertEqual(sync_regions(self.area).deactivated, 1)
        self.region.refresh_from_db()
        other.refresh_from_db()
        self.assertFalse(self.region.is_active)
        self.assertTrue(other.is_active)
        with patch('catalogs.nova_poshta.fetch_regions', return_value={self.region.ref: {'description': self.region.description, 'region_type': self.region.region_type}}):
            sync_regions(self.area)
        self.region.refresh_from_db()
        self.assertTrue(self.region.is_active)

    def test_settlement_scope_errors_and_reactivation(self):
        with patch('catalogs.nova_poshta._fetch_catalog', return_value=[self.item]):
            sync_settlements(self.area)
        other_area = NovaPoshtaArea.objects.create(ref=UUID(int=1), description='Другая')
        other_data = {**self.item, 'Ref': str(UUID(int=3)), 'Area': str(other_area.ref), 'Region': ''}
        with patch('catalogs.nova_poshta._fetch_catalog', return_value=[other_data]):
            sync_settlements(other_area)
        with patch('catalogs.nova_poshta._fetch_catalog', side_effect=NovaPoshtaError('Ошибка')):
            with self.assertRaises(NovaPoshtaError):
                sync_settlements(self.area)
        self.assertEqual(NovaPoshtaSettlement.objects.filter(is_active=True).count(), 2)
        with patch('catalogs.nova_poshta._fetch_catalog', return_value=[]):
            self.assertEqual(sync_settlements(self.area).deactivated, 1)
        self.assertTrue(NovaPoshtaSettlement.objects.get(area=other_area).is_active)
        self.assertFalse(NovaPoshtaSettlement.objects.get(area=self.area).is_active)
        with patch('catalogs.nova_poshta._fetch_catalog', return_value=[self.item]):
            sync_settlements(self.area)
        self.assertTrue(NovaPoshtaSettlement.objects.get(area=self.area).is_active)
        with patch('catalogs.nova_poshta._fetch_catalog', return_value=[]):
            self.assertEqual(sync_settlements().deactivated, 2)
        self.assertEqual(NovaPoshtaSettlement.objects.count(), 2)
