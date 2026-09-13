from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from catalogs.models import Contractor, Organization, OurBankAccount, ContractorBankAccount
from documents.models import PaymentOrderIn


class IncomingPaymentTests(TestCase):
    def setUp(self):
        self.enterContext(translation.override("ru"))
        self.organization = Organization.objects.create(name="Получатель")
        self.contractor = Contractor.objects.create(last_name="Плательщик")
        self.payment = PaymentOrderIn(
            organization=self.organization, contractor=self.contractor,
            payment_number="123", amount=Decimal("100"),
        )

    def test_categories_and_optional_bank_identifiers(self):
        for category in PaymentOrderIn.Category.values:
            self.payment.category = category
            self.payment.full_clean()
        self.payment.category = "invalid"
        with self.assertRaises(ValidationError):
            self.payment.full_clean()

    def test_required_number_and_positive_amount(self):
        for field, value in (("payment_number", ""), ("amount", 0), ("amount", -1)):
            with self.subTest(field=field, value=value):
                original = getattr(self.payment, field)
                setattr(self.payment, field, value)
                with self.assertRaises(ValidationError) as error:
                    self.payment.full_clean()
                self.assertIn(field, error.exception.message_dict)
                setattr(self.payment, field, original)

    def test_accounts_must_belong_to_selected_parties(self):
        other_org = Organization.objects.create(name="Чужая организация")
        self.payment.our_bank_account = OurBankAccount.objects.create(
            organization=other_org, iban="UA111",
        )
        with self.assertRaises(ValidationError) as error:
            self.payment.clean()
        self.assertIn("our_bank_account", error.exception.message_dict)
        self.payment.our_bank_account = None
        other = Contractor.objects.create(last_name="Чужой плательщик")
        self.payment.contractor_bank_account = ContractorBankAccount.objects.create(
            contractor=other, iban="UA222",
        )
        with self.assertRaises(ValidationError) as error:
            self.payment.clean()
        self.assertIn("contractor_bank_account", error.exception.message_dict)

    def test_posting_requires_accounts_and_uses_defaults(self):
        self.payment.is_applied = True
        with self.assertRaises(ValidationError):
            self.payment.clean()
        ours = OurBankAccount.objects.create(organization=self.organization, iban="UA333", is_default=True)
        theirs = ContractorBankAccount.objects.create(contractor=self.contractor, iban="UA444")
        self.contractor.primary_account = theirs
        self.contractor.save()
        self.payment.clean()
        self.payment.save()
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.our_bank_account_id, ours.pk)
        self.assertEqual(self.payment.contractor_bank_account_id, theirs.pk)

    def test_admin_can_create_draft(self):
        user = get_user_model().objects.create_superuser("incoming-admin", password="test")
        self.client.force_login(user)
        url = reverse("admin:documents_paymentorderin_add")
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {
            "organization": self.organization.pk, "contractor": self.contractor.pk,
            "paymentinitem_set-TOTAL_FORMS": "0", "paymentinitem_set-INITIAL_FORMS": "0",
            "payment_number": "BANK-123", "category": "other", "amount": "123.45", "_save": "Save",
        })
        self.assertEqual(response.status_code, 302)
        payment = PaymentOrderIn.objects.get()
        self.assertEqual(payment.amount, Decimal("123.45"))
        self.assertFalse(payment.is_applied)
