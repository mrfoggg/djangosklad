from uuid import UUID
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import NovaPoshtaArea, NovaPoshtaRegion
from .nova_poshta import sync_settlements
from .test_nova_poshta_settlements import SettlementDataMixin


@override_settings(LANGUAGE_CODE='ru')
class NovaPoshtaInlineTests(SettlementDataMixin, TestCase):
    def test_paginated_readonly_relations(self):
        self.client.force_login(get_user_model().objects.create_user('inline-admin', is_staff=True, is_superuser=True))
        for index in range(21):
            NovaPoshtaRegion.objects.create(ref=UUID(int=index + 1), area=self.area, description=f'Район {index:02}', region_type='район')
        other_area = NovaPoshtaArea.objects.create(ref=UUID(int=100), description='Другая')
        NovaPoshtaRegion.objects.create(ref=UUID(int=101), area=other_area, description='Чужой район', region_type='район')
        with patch('catalogs.nova_poshta._fetch_catalog', return_value=[
            {**self.item, 'Ref': str(UUID(int=index + 1000)), 'Description': f'Село {index:02}'}
            for index in range(21)
        ]):
            sync_settlements(self.area)
        for model_name, parent, total in (
            ('novaposhtaarea', self.area, 22), ('novaposhtaregion', self.region, 21),
        ):
            url = reverse(f'admin:catalogs_{model_name}_change', args=[parent.pk])
            first = self.client.get(url)
            self.assertEqual(first.status_code, 200)
            inline = first.context['inline_admin_formsets'][0]
            formset = inline.formset
            self.assertEqual(formset.paginator.count, total)
            self.assertEqual(len(formset.forms), 20)
            self.assertFalse(inline.has_add_permission)
            self.assertFalse(inline.has_change_permission)
            self.assertFalse(inline.has_delete_permission)
            first_ids = {form.instance.pk for form in formset.forms}
            second = self.client.get(url, {formset.get_pagination_key(): 2})
            next_formset = second.context['inline_admin_formsets'][0].formset
            self.assertEqual(len(next_formset.forms), total - 20)
            self.assertFalse(first_ids & {form.instance.pk for form in next_formset.forms})
            child = formset.forms[0].instance
            self.assertContains(first, reverse(f'admin:catalogs_{child._meta.model_name}_change', args=[child.pk]))
