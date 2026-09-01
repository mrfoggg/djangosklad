from django.contrib.auth import get_user_model
from django.forms import modelform_factory
from django.test import TestCase
from django.urls import reverse
from djmoney.money import Money

from catalogs.models import (
    Contractor,
    MeasurementUnit,
    Organization,
    OurBankAccount,
    Product,
    ProductSupplier,
    Warehouse,
)

from .admin import (
    OrderItemInlineForm,
    PurchaseInvoiceItemInlineForm,
    SalesInvoiceItemInlineForm,
)
from .models import (
    CustomerOrder,
    InvoiceItem,
    OrderItem,
    PurchaseOrder,
    SalesInvoice,
    SalesInvoiceItem,
    SupplierPriceItem,
    SupplierPriceList,
)


class OrderItemInlineFormTests(TestCase):
    @staticmethod
    def make_form(*, purchase_applied=False, customer_applied=False):
        purchase_order = PurchaseOrder(pk=1, is_applied=purchase_applied)
        customer_order = CustomerOrder(pk=1, is_applied=customer_applied)
        instance = OrderItem(
            pk=1,
            purchase_order=purchase_order,
            customer_order=customer_order,
        )
        form_class = modelform_factory(
            OrderItem,
            form=OrderItemInlineForm,
            fields="__all__",
        )
        return form_class(instance=instance)

    def test_fields_locked_by_applied_purchase_order(self):
        form = self.make_form(purchase_applied=True)

        for name in (
            "product",
            "rrp",
            "quantity",
            "warehouse",
            "organization",
            "purchase_price",
            "customer_order",
        ):
            self.assertTrue(form.fields[name].disabled, name)

        for name in ("customer_price", "payment_method_customer", "purchase_order"):
            self.assertFalse(form.fields[name].disabled, name)

    def test_fields_locked_by_applied_customer_order(self):
        form = self.make_form(customer_applied=True)

        for name in (
            "product",
            "rrp",
            "quantity",
            "warehouse",
            "organization",
            "customer_price",
            "payment_method_customer",
            "purchase_order",
        ):
            self.assertTrue(form.fields[name].disabled, name)

        for name in ("purchase_price", "customer_order"):
            self.assertFalse(form.fields[name].disabled, name)

    def test_all_restricted_fields_locked_when_both_orders_are_applied(self):
        form = self.make_form(purchase_applied=True, customer_applied=True)

        for name in (
            "product",
            "rrp",
            "quantity",
            "warehouse",
            "organization",
            "purchase_price",
            "customer_price",
            "payment_method_customer",
            "purchase_order",
            "customer_order",
        ):
            self.assertTrue(form.fields[name].disabled, name)

    def test_quantity_precision_comes_from_products_unit(self):
        unit = MeasurementUnit.objects.create(
            code="kg", name="Килограмм", symbol="кг", decimal_places=3
        )
        product = Product.objects.create(name="Весовой товар", sku="weighted", unit=unit)
        warehouse = Warehouse.objects.create(name="Виртуальный", is_virtual=True)
        form_class = modelform_factory(
            OrderItem,
            form=OrderItemInlineForm,
            fields="__all__",
        )

        valid_form = form_class(
            data={
                "product": product.pk,
                "quantity": "1.234",
                "warehouse": warehouse.pk,
                "payment_method_customer": OrderItem.CustomerPaymentMethod.PREPAID,
            }
        )
        invalid_form = form_class(
            data={
                "product": product.pk,
                "quantity": "1.2345",
                "warehouse": warehouse.pk,
                "payment_method_customer": OrderItem.CustomerPaymentMethod.PREPAID,
            }
        )

        self.assertTrue(valid_form.is_valid(), valid_form.errors)
        self.assertFalse(invalid_form.is_valid())
        self.assertIn("quantity", invalid_form.errors)

    def test_product_option_contains_quantity_step_metadata(self):
        unit = MeasurementUnit.objects.create(
            code="m", name="Метр", symbol="м", decimal_places=2
        )
        Product.objects.create(name="Кабель", sku="cable", unit=unit)
        form_class = modelform_factory(
            OrderItem,
            form=OrderItemInlineForm,
            fields="__all__",
        )

        product_html = str(form_class()["product"])

        self.assertIn('data-quantity-decimal-places="2"', product_html)
        self.assertIn('data-unit-symbol="м"', product_html)


class OrderItemPaymentMethodTests(TestCase):
    def test_customer_payment_choices(self):
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
        self.assertFalse(form.fields["order_item"].queryset.query.where.children)


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
