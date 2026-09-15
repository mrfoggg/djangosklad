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

    def test_area_settlements_tab_includes_unassigned_regions(self):
        self.client.force_login(get_user_model().objects.create_user('area-tabs', is_staff=True, is_superuser=True))
        with patch('catalogs.nova_poshta._fetch_catalog', return_value=[
            {**self.item, 'Ref': str(UUID(int=index + 2000)), 'Region': '' if index == 0 else self.item['Region']}
            for index in range(21)
        ]):
            sync_settlements(self.area)
        other = NovaPoshtaArea.objects.create(ref=UUID(int=3000), description='Другая')
        with patch('catalogs.nova_poshta._fetch_catalog', return_value=[{**self.item, 'Ref': str(UUID(int=3001)), 'Area': str(other.pk), 'Region': ''}]):
            sync_settlements(other)
        url = reverse('admin:catalogs_novaposhtaarea_change', args=[self.area.pk])
        response = self.client.get(url)
        regions, settlements = response.context['inline_admin_formsets']
        self.assertTrue(regions.opts.tab)
        self.assertTrue(settlements.opts.tab)
        self.assertEqual(settlements.formset.paginator.count, 21)
        self.assertEqual(len(settlements.formset.forms), 20)
        self.assertNotEqual(regions.formset.get_pagination_key(), settlements.formset.get_pagination_key())
        self.assertTrue(any(form.instance.region_id is None for form in settlements.formset.forms))
        second = self.client.get(url, {settlements.formset.get_pagination_key(): 2})
        regions2, settlements2 = second.context['inline_admin_formsets']
        self.assertEqual(regions2.formset.page.number, 1)
        self.assertEqual(len(settlements2.formset.forms), 1)
        self.assertEqual(settlements2.formset.forms[0].instance.area_id, self.area.pk)

    def test_ukrainian_alphabet_across_pages(self):
        self.client.force_login(get_user_model().objects.create_user('alphabet-admin', is_staff=True, is_superuser=True))
        self.region.description = 'Ізюмський'
        self.region.save()
        names = ['Яворівський', 'Їжаківський', 'Єланецький', 'Гадяцький', 'Ґрунський', 'Балаклійський']
        names += [f'Долинський {index:02}' for index in range(17)]
        for index, name in enumerate(names):
            NovaPoshtaRegion.objects.create(ref=UUID(int=index + 5000), area=self.area, description=name, region_type='район')
        url = reverse('admin:catalogs_novaposhtaarea_change', args=[self.area.pk])
        first = self.client.get(url).context['inline_admin_formsets'][0].formset
        second = self.client.get(url, {first.get_pagination_key(): 2}).context['inline_admin_formsets'][0].formset
        actual = [form.instance.description for form in [*first.forms, *second.forms]]
        self.assertEqual(actual, ['Балаклійський', 'Гадяцький', 'Ґрунський'] + [f'Долинський {index:02}' for index in range(17)] + ['Єланецький', 'Ізюмський', 'Їжаківський', 'Яворівський'])

    def test_catalog_changelists_use_ukrainian_order(self):
        from django.contrib import admin
        from django.test import RequestFactory
        from catalogs.models import NovaPoshtaSettlement
        user = get_user_model().objects.create_user('list-alphabet', is_staff=True, is_superuser=True)
        names = ['Ізюмський', 'Яворівський', 'Балаклійський', 'Єланецький']
        with patch('catalogs.nova_poshta._fetch_catalog', return_value=[
            {**self.item, 'Ref': str(UUID(int=index + 6000)), 'Description': name}
            for index, name in enumerate(names)
        ]):
            sync_settlements(self.area)
        for index, name in enumerate(names):
            NovaPoshtaArea.objects.create(ref=UUID(int=index + 6000), description=name)
            NovaPoshtaRegion.objects.create(ref=UUID(int=index + 6000), area=self.area, description=name, region_type='район')
        for model in (NovaPoshtaArea, NovaPoshtaRegion, NovaPoshtaSettlement):
            for direction, expected in (('0', ['Балаклійський', 'Єланецький', 'Ізюмський', 'Яворівський']), ('-0', ['Яворівський', 'Ізюмський', 'Єланецький', 'Балаклійський'])):
                request = RequestFactory().get('/', {'o': direction})
                request.user = user
                changelist = admin.site._registry[model].get_changelist_instance(request)
                actual = [obj.description for obj in changelist.result_list if obj.description in names]
                self.assertEqual(actual, expected)
