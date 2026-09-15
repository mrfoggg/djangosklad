from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model

from .models import (
    ContactPerson, ContactPersonPhone, ContactRole, Contractor,
    ContractorContactPerson, ContractorPhone, PhoneNumber,
)


@override_settings(LANGUAGE_CODE="ru")
class ContactTests(TestCase):
    def setUp(self):
        self.contractor = Contractor.objects.create(last_name="Компания")
        self.person = ContactPerson.objects.create(last_name="Иванов", first_name="Иван")
        self.phone = PhoneNumber.objects.create(number="0501234567")

    def test_normalization_validation_and_messengers(self):
        self.phone.refresh_from_db()
        self.assertEqual(self.phone.number.as_e164, "+380501234567")
        self.assertIsNone(self.phone.has_telegram)
        with self.assertRaises(ValidationError):
            PhoneNumber.objects.create(number="+380 50 123 45 67")
        with self.assertRaises(ValidationError):
            PhoneNumber.objects.create(number="123")
        self.phone.has_viber = True
        self.phone.has_whatsapp = False
        self.phone.save()
        self.phone.refresh_from_db()
        self.assertTrue(self.phone.has_viber)
        self.assertFalse(self.phone.has_whatsapp)

    def test_operator_is_computed_from_current_number(self):
        self.assertEqual(self.phone.operator_name, "Vodafone")
        self.phone.number = "0671234567"
        self.assertEqual(self.phone.operator_name, "Kyivstar")
        self.assertEqual(PhoneNumber(number="").operator_name, "")
        self.assertEqual(PhoneNumber(number="123").operator_name, "")
        self.assertEqual(PhoneNumber(number="+380441234567").operator_name, "")

    def test_type_and_geographical_description(self):
        self.assertEqual(self.phone.number_type_label, "Мобильный")
        self.phone.number = "+380441234567"
        self.assertEqual(self.phone.number_type_label, "Стационарный")
        self.assertTrue(self.phone.region_description)
        for value in ("", "123"):
            phone = PhoneNumber(number=value)
            self.assertEqual(phone.number_type_label, "")
            self.assertEqual(phone.region_description, "")

    def test_shared_phone_and_roles(self):
        ContractorPhone.objects.create(contractor=self.contractor, phone=self.phone)
        ContactPersonPhone.objects.create(contact_person=self.person, phone=self.phone, for_delivery=True)
        link = ContractorContactPerson.objects.create(contractor=self.contractor, contact_person=self.person)
        link.roles.set(ContactRole.objects.filter(code__in=["director", "parcel_recipient"]))
        self.assertEqual(link.roles.count(), 2)
        self.assertEqual(self.person.phones.get(), self.contractor.phones.get())
        with self.assertRaises(IntegrityError), transaction.atomic():
            ContractorContactPerson.objects.create(contractor=self.contractor, contact_person=self.person)

    def test_phone_constraints_for_both_owner_types(self):
        other_phone = PhoneNumber.objects.create(number="0671234567")
        for model, owner in ((ContractorPhone, {"contractor": self.contractor}), (ContactPersonPhone, {"contact_person": self.person})):
            model.objects.create(**owner, phone=self.phone, is_primary_for_communication=True)
            with self.assertRaises(IntegrityError), transaction.atomic():
                model.objects.create(**owner, phone=self.phone)
            with self.assertRaises(IntegrityError), transaction.atomic():
                model.objects.create(**owner, phone=other_phone, is_primary_for_communication=True)
            with self.assertRaises(IntegrityError), transaction.atomic():
                model.objects.create(**owner, phone=other_phone, is_primary_for_delivery=True)
            model.objects.create(**owner, phone=other_phone, for_delivery=True, is_primary_for_delivery=True)

    def test_admin_pages(self):
        user = get_user_model().objects.create_superuser(username="admin", password="test")
        self.client.force_login(user)
        for model, obj in ((Contractor, self.contractor), (ContactPerson, self.person), (PhoneNumber, self.phone)):
            response = self.client.get(reverse(f"admin:catalogs_{model._meta.model_name}_change", args=[obj.pk]))
            self.assertEqual(response.status_code, 200)
        self.assertNotIn("number", admin.site._registry[PhoneNumber].get_readonly_fields(None, self.phone))

    def test_edit_shared_number_in_admin(self):
        ContractorPhone.objects.create(contractor=self.contractor, phone=self.phone)
        ContactPersonPhone.objects.create(contact_person=self.person, phone=self.phone)
        self.client.force_login(get_user_model().objects.create_superuser(username="editor", password="test"))
        url = reverse("admin:catalogs_phonenumber_change", args=[self.phone.pk])
        response = self.client.get(url)
        self.assertContains(response, 'name="number_1"')
        self.assertContains(response, reverse("admin:catalogs_contractor_change", args=[self.contractor.pk]))
        self.assertContains(response, reverse("admin:catalogs_contactperson_change", args=[self.person.pk]))
        response = self.client.post(url, {"number_0": "UA", "number_1": "0671234567", "_save": "1"})
        self.assertEqual(response.status_code, 302)
        self.phone.refresh_from_db()
        self.assertEqual(self.phone.number.as_e164, "+380671234567")
        self.assertEqual(self.contractor.phones.get().pk, self.phone.pk)
        self.assertEqual(self.person.phones.get().number, self.phone.number)


@override_settings(LANGUAGE_CODE="ru")
class PhoneNumberFormTests(TestCase):
    def test_default_country_follows_settings(self):
        from .forms import PhoneNumberForm

        for region in ("UA", "PL"):
            with self.settings(PHONENUMBER_DEFAULT_REGION=region):
                form = PhoneNumberForm()
                self.assertEqual(form["number"].value(), [region, None])
                self.assertIn(f'value="{region}" selected', str(form["number"]))

    def test_split_number_saved_in_international_format(self):
        from .forms import PhoneNumberForm

        for region, national, expected in (
            ("UA", "0501234567", "+380501234567"),
            ("PL", "512345678", "+48512345678"),
        ):
            form = PhoneNumberForm(data={"number_0": region, "number_1": national})
            self.assertTrue(form.is_valid(), form.errors)
            phone = form.save()
            phone.refresh_from_db()
            self.assertEqual(phone.number.as_e164, expected)

    def test_admin_add_uses_split_field(self):
        user = get_user_model().objects.create_superuser(username="admin", password="test")
        self.client.force_login(user)
        response = self.client.get(reverse("admin:catalogs_phonenumber_add"))
        self.assertContains(response, 'name="number_0"')
        self.assertContains(response, 'name="number_1"')
        self.assertContains(response, 'value="UA" selected')
