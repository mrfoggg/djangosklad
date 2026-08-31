from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from djmoney.money import Money

from catalogs.models import (
    Contractor,
    Organization,
    OurBankAccount,
    Product,
    ProductSupplier,
)

from .admin import PurchaseInvoiceItemInlineForm, SalesInvoiceItemInlineForm
from .models import (
    InvoiceItem,
    OrderItem,
    SalesInvoice,
    SalesInvoiceItem,
    SupplierPriceItem,
    SupplierPriceList,
)


class OrderItemPaymentMethodTests(TestCase):
    def test_purchase_and_customer_payment_choices_are_separate(self):
        self.assertEqual(
            list(OrderItem.PurchasePaymentMethod.labels),
            ["Предоплата", "Отсрочка платежа"],
        )
        self.assertEqual(
            list(OrderItem.CustomerPaymentMethod.labels),
            ["Оплата по счету", "Постоплата"],
        )


class PurchaseInvoiceItemInlineFormTests(TestCase):
    def test_order_item_is_editable_for_new_invoice_item(self):
        form = PurchaseInvoiceItemInlineForm(instance=InvoiceItem())

        self.assertFalse(form.fields["order_item"].disabled)
        self.assertIn(
            "invoiceitem",
            str(form.fields["order_item"].queryset.query).lower(),
        )

    def test_order_item_is_disabled_after_invoice_item_is_saved(self):
        form = PurchaseInvoiceItemInlineForm(
            instance=InvoiceItem(pk=1, order_item_id=42)
        )

        self.assertTrue(form.fields["order_item"].disabled)
        self.assertEqual(
            form.fields["order_item"].queryset.query.where.children[0].rhs,
            42,
        )


class SalesInvoiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="Организация счета")
        cls.customer = Contractor.objects.create(last_name="Покупатель")
        cls.bank_account = OurBankAccount.objects.create(
            organization=cls.organization,
            bank_name="Банк",
            iban="UA123456789012345678901234567",
            is_default=True,
        )

    def test_default_organization_bank_account_is_selected(self):
        invoice = SalesInvoice.objects.create(
            organization=self.organization,
            customer=self.customer,
        )

        self.assertEqual(invoice.bank_account, self.bank_account)

    def test_order_item_is_locked_after_sales_invoice_item_is_saved(self):
        form = SalesInvoiceItemInlineForm(
            instance=SalesInvoiceItem(pk=1, order_item_id=42)
        )

        self.assertTrue(form.fields["order_item"].disabled)
        self.assertEqual(
            form.fields["order_item"].queryset.query.where.children[0].rhs,
            42,
        )


class MainSupplierPriceAjaxTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="admin", password="test-password"
        )
        cls.supplier = Contractor.objects.create(
            last_name="Тестовый поставщик",
            default_price_type=Contractor.PriceType.WHOLESALE,
        )
        cls.product = Product.objects.create(name="Тестовый товар", sku="test-product")
        product_supplier = ProductSupplier.objects.create(
            product=cls.product,
            supplier=cls.supplier,
        )
        cls.product.main_supplier = product_supplier
        cls.product.save()
        price_list = SupplierPriceList.objects.create(
            supplier=cls.supplier,
            is_applied=True,
        )
        SupplierPriceItem.objects.create(
            document=price_list,
            product=cls.product,
            price=Money("150.00", "UAH"),
            small_wholesale_price=Money("110.00", "UAH"),
            wholesale_price=Money("100.00", "UAH"),
            large_wholesale_price=Money("90.00", "UAH"),
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_uses_products_main_supplier_and_its_default_price_type(self):
        response = self.client.get(
            reverse("get_latest_price"),
            {"product_id": self.product.pk, "use_main_supplier": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        self.assertEqual(response.json()["price"], "100.00")
        self.assertEqual(response.json()["rrp"], "150.00")
        self.assertEqual(response.json()["supplier"], str(self.supplier))

    def test_returns_information_when_product_has_no_main_supplier(self):
        product = Product.objects.create(name="Без поставщика", sku="without-supplier")

        response = self.client.get(
            reverse("get_latest_price"),
            {"product_id": product.pk, "use_main_supplier": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "info")
        self.assertEqual(response.json()["price"], "0")
