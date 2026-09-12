from datetime import UTC, datetime
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory, modelform_factory
from django.test import RequestFactory, TestCase
from django.urls import reverse
from djmoney.money import Money

from catalogs.models import (
    Contractor,
    ContractorBankAccount,
    MeasurementUnit,
    Organization,
    OurBankAccount,
    Product,
    ProductSupplier,
    RetailStore,
    Warehouse,
)

from .admin import (
    OrderItemInlineForm,
    PaymentOrderOutForm,
    PaymentOutItemInline,
    PaymentOutItemInlineForm,
    PaymentOutItemInlineFormSet,
    PurchaseInvoiceItemInlineForm,
    SalesInvoiceItemInlineForm,
)
from .models import (
    CustomerOrder,
    InvoiceItem,
    OrderItem,
    PaymentOrderOut,
    PaymentOutItem,
    PurchaseInvoice,
    PurchaseOrder,
    RetailPriceItem,
    RetailPriceList,
    SalesInvoice,
    SalesInvoiceItem,
    SupplierPriceItem,
    SupplierPriceList,
)


class PaymentOrderOutBankAccountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="Организация отправителя")
        cls.other_organization = Organization.objects.create(name="Другая организация")
        cls.contractor = Contractor.objects.create(last_name="Получатель")
        cls.default_account = OurBankAccount.objects.create(
            organization=cls.organization,
            bank_name="Основной банк",
            iban="UA111111111111111111111111111",
            is_default=True,
        )
        cls.other_account = OurBankAccount.objects.create(
            organization=cls.other_organization,
            bank_name="Чужой банк",
            iban="UA222222222222222222222222222",
        )
        cls.contractor_account = ContractorBankAccount.objects.create(
            contractor=cls.contractor,
            bank_name="Банк получателя",
            iban="UA333333333333333333333333333",
        )
        cls.contractor.primary_account = cls.contractor_account
        cls.contractor.save()
        cls.other_contractor = Contractor.objects.create(last_name="Другой получатель")
        cls.other_contractor_account = ContractorBankAccount.objects.create(
            contractor=cls.other_contractor,
            bank_name="Банк другого получателя",
            iban="UA444444444444444444444444444",
        )

    def test_default_organization_account_is_selected_on_save(self):
        payment = PaymentOrderOut.objects.create(
            organization=self.organization,
            contractor=self.contractor,
            amount="100.00",
        )

        self.assertEqual(payment.our_bank_account, self.default_account)
        self.assertEqual(payment.contractor_bank_account, self.contractor_account)

    def test_account_from_another_organization_is_rejected(self):
        payment = PaymentOrderOut(
            organization=self.organization,
            contractor=self.contractor,
            amount="100.00",
            our_bank_account=self.other_account,
        )

        with self.assertRaises(ValidationError):
            payment.full_clean()

    def test_account_options_contain_organization_metadata(self):
        form = PaymentOrderOutForm()

        account_html = str(form["our_bank_account"])

        self.assertIn(
            f'data-organization-id="{self.organization.pk}"', account_html
        )
        self.assertIn('data-is-default="true"', account_html)

    def test_account_from_another_contractor_is_rejected(self):
        payment = PaymentOrderOut(
            organization=self.organization,
            contractor=self.contractor,
            amount="100.00",
            our_bank_account=self.default_account,
            contractor_bank_account=self.other_contractor_account,
        )

        with self.assertRaises(ValidationError):
            payment.full_clean()

    def test_contractor_account_options_contain_contractor_metadata(self):
        form = PaymentOrderOutForm()

        account_html = str(form["contractor_bank_account"])

        self.assertIn(f'data-contractor-id="{self.contractor.pk}"', account_html)
        self.assertIn('data-is-primary="true"', account_html)


class PaymentOutItemTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = get_user_model().objects.create_superuser(
            username="payment-admin",
            password="test-password",
        )
        cls.organization = Organization.objects.create(name="Организация платежа")
        cls.supplier = Contractor.objects.create(
            last_name="Поставщик для оплаты", is_supplier=True
        )
        cls.product = Product.objects.create(name="Оплачиваемый товар", sku="payable")
        cls.warehouse = Warehouse.objects.create(name="Склад оплаты", is_virtual=True)
        cls.order_item = OrderItem.objects.create(
            product=cls.product,
            warehouse=cls.warehouse,
            quantity=1,
            purchase_price="100.00",
        )
        cls.invoice = PurchaseInvoice.objects.create(
            supplier=cls.supplier,
            organization=cls.organization,
        )
        InvoiceItem.objects.create(invoice=cls.invoice, order_item=cls.order_item)

    def create_payment(self, amount, *, is_applied=False):
        return PaymentOrderOut.objects.create(
            organization=self.organization,
            contractor=self.supplier,
            amount=amount,
            is_applied=is_applied,
        )

    def test_blank_amount_is_filled_with_unpaid_invoice_balance(self):
        paid = self.create_payment("40.00", is_applied=True)
        PaymentOutItem.objects.create(
            payment=paid,
            invoice=self.invoice,
            amount="40.00",
        )
        current_payment = self.create_payment("60.00")

        item = PaymentOutItem.objects.create(
            payment=current_payment,
            invoice=self.invoice,
            amount=None,
        )

        self.assertEqual(item.amount, Decimal("60.00"))

    def test_zero_amount_is_filled_with_unpaid_invoice_balance(self):
        payment = self.create_payment("100.00")

        item = PaymentOutItem.objects.create(
            payment=payment,
            invoice=self.invoice,
            amount=Decimal("0.00"),
        )

        self.assertEqual(item.amount, Decimal("100.00"))

    def test_explicit_amount_is_not_replaced(self):
        payment = self.create_payment("25.00")

        item = PaymentOutItem.objects.create(
            payment=payment,
            invoice=self.invoice,
            amount="25.00",
        )
        item.refresh_from_db()

        self.assertEqual(item.amount, Decimal("25.00"))

    def test_invoice_choice_displays_invoice_total(self):
        form = PaymentOutItemInlineForm()

        label = form.fields["invoice"].label_from_instance(
            form.fields["invoice"].queryset.get(pk=self.invoice.pk)
        )

        self.assertIn("100", label)
        self.assertIn("грн", label)

    def get_payment_status(self, item):
        inline = PaymentOutItemInline(PaymentOrderOut, admin.site)
        request = RequestFactory().get("/")
        request.user = self.admin_user
        annotated_item = inline.get_queryset(request).get(pk=item.pk)
        return str(inline.payment_status(annotated_item))

    def test_inline_displays_underpayment_for_draft_payment(self):
        payment = self.create_payment("25.00")
        item = PaymentOutItem.objects.create(
            payment=payment,
            invoice=self.invoice,
            amount="25.00",
        )

        status = self.get_payment_status(item)

        self.assertIn("Недоплата", status)
        self.assertIn("75", status)

    def test_inline_displays_paid_for_fully_paid_invoice(self):
        payment = self.create_payment("100.00", is_applied=True)
        item = PaymentOutItem.objects.create(
            payment=payment,
            invoice=self.invoice,
            amount="100.00",
        )

        self.assertIn("Оплачено", self.get_payment_status(item))

    def test_inline_displays_overpayment(self):
        payment = self.create_payment("120.00", is_applied=True)
        item = PaymentOutItem.objects.create(
            payment=payment,
            invoice=self.invoice,
            amount="120.00",
        )

        status = self.get_payment_status(item)

        self.assertIn("Переплата", status)
        self.assertIn("20", status)


class PaymentAllocationValidationTests(PaymentOutItemTests):
    def formset(self, payment, rows, **parent):
        payment.amount = Decimal(payment.amount)
        factory = inlineformset_factory(
            PaymentOrderOut, PaymentOutItem,
            form=PaymentOutItemInlineForm, formset=PaymentOutItemInlineFormSet,
            fields=("invoice", "amount"), extra=0,
        )
        initial = payment.paymentoutitem_set.count() if payment.pk else 0
        data = {
            "contractor": str(payment.contractor_id),
            "organization": str(payment.organization_id),
            "paymentoutitem_set-TOTAL_FORMS": str(len(rows)),
            "paymentoutitem_set-INITIAL_FORMS": str(initial),
            **parent,
        }
        for i, row in enumerate(rows):
            for key, value in row.items():
                data[f"paymentoutitem_set-{i}-{key}"] = str(value)
        return factory(data=data, instance=payment)

    def test_automatic_allocation_cannot_exceed_payment(self):
        for amount in ("", "0", "101"):
            with self.subTest(amount=amount):
                payment = self.create_payment("50.00")
                forms = self.formset(payment, [{"invoice": self.invoice.pk, "amount": amount}])
                self.assertFalse(forms.is_valid())
                self.assertIn("больше суммы платежа", str(forms.non_form_errors()))

    def test_partial_allocation_and_unallocated_balance_are_allowed(self):
        payment = self.create_payment("100.00")
        forms = self.formset(payment, [{"invoice": self.invoice.pk, "amount": "40"}])
        self.assertTrue(forms.is_valid(), forms.errors)
        forms.save()
        self.assertEqual(payment.paymentoutitem_set.get().amount, Decimal("40"))

    def test_foreign_supplier_and_organization_are_rejected(self):
        payment = self.create_payment("100.00")
        other_supplier = Contractor.objects.create(last_name="Чужой", is_supplier=True)
        other_org = Organization.objects.create(name="Чужая")
        for field, value in (("supplier", other_supplier), ("organization", other_org)):
            with self.subTest(field=field):
                setattr(self.invoice, field, value)
                self.invoice.save()
                forms = self.formset(payment, [{"invoice": self.invoice.pk, "amount": "10"}])
                self.assertFalse(forms.is_valid())
                self.assertIn("invoice", forms.errors[0])
                self.invoice.supplier = self.supplier
                self.invoice.organization = self.organization
                self.invoice.save()

    def test_fully_paid_invoice_is_rejected_for_new_row(self):
        paid = self.create_payment("100.00", is_applied=True)
        PaymentOutItem.objects.create(payment=paid, invoice=self.invoice, amount="100")
        payment = self.create_payment("100.00")
        forms = self.formset(payment, [{"invoice": self.invoice.pk, "amount": "10"}])
        self.assertFalse(forms.is_valid())
        self.assertIn("invoice", forms.errors[0])

    def test_existing_fully_paid_invoice_can_be_edited(self):
        payment = self.create_payment("100.00", is_applied=True)
        item = PaymentOutItem.objects.create(payment=payment, invoice=self.invoice, amount="100")
        forms = self.formset(payment, [{"id": item.pk, "invoice": self.invoice.pk, "amount": "0"}])
        self.assertTrue(forms.is_valid(), forms.errors)
        forms.save()
        item.refresh_from_db()
        self.assertEqual(item.amount, Decimal("100"))

    def test_deleted_allocation_is_excluded(self):
        payment = self.create_payment("100.00", is_applied=True)
        item = PaymentOutItem.objects.create(payment=payment, invoice=self.invoice, amount="100")
        forms = self.formset(payment, [
            {"id": item.pk, "invoice": self.invoice.pk, "amount": "100", "DELETE": "on"},
            {"invoice": self.invoice.pk, "amount": ""},
        ])
        self.assertTrue(forms.is_valid(), forms.errors)
        forms.save()
        self.assertEqual(payment.paymentoutitem_set.get().amount, Decimal("100"))

    def test_combined_rows_cannot_exceed_payment(self):
        payment = self.create_payment("100.00")
        forms = self.formset(payment, [
            {"invoice": self.invoice.pk, "amount": "60"},
            {"invoice": self.invoice.pk, "amount": "60"},
        ])
        self.assertFalse(forms.is_valid())

    def test_negative_allocation_is_rejected(self):
        payment = self.create_payment("100.00")
        forms = self.formset(payment, [{"invoice": self.invoice.pk, "amount": "-1"}])
        self.assertFalse(forms.is_valid())
        self.assertIn("amount", forms.errors[0])

    def test_automatic_zero_is_not_recalculated_during_save(self):
        payment = self.create_payment("100.00")
        forms = self.formset(payment, [
            {"invoice": self.invoice.pk, "amount": "100"},
            {"invoice": self.invoice.pk, "amount": ""},
        ])
        self.assertTrue(forms.is_valid(), forms.errors)
        forms.save()
        self.assertEqual(
            sum(payment.paymentoutitem_set.values_list("amount", flat=True)),
            Decimal("100"),
        )

    def test_admin_rejects_excess_and_saves_valid_automatic_allocation(self):
        self.client.force_login(self.admin_user)
        data = {
            "organization": self.organization.pk,
            "contractor": self.supplier.pk,
            "amount": "50.00",
            "bank_commission": "0",
            "category": PaymentOrderOut.Category.GOODS,
            "payment_number": "000123/Б",
            "paymentoutitem_set-TOTAL_FORMS": "1",
            "paymentoutitem_set-INITIAL_FORMS": "0",
            "paymentoutitem_set-0-invoice": self.invoice.pk,
            "paymentoutitem_set-0-amount": "",
            "_save": "Сохранить",
        }
        url = reverse("admin:documents_paymentorderout_add")
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "больше суммы платежа")
        self.assertFalse(PaymentOrderOut.objects.exists())
        data["amount"] = "100.00"
        data["payment_number"] = ""
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("payment_number", response.context["adminform"].form.errors)
        self.assertFalse(PaymentOrderOut.objects.exists())
        data["payment_number"] = "000123/Б"
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PaymentOutItem.objects.get().amount, Decimal("100"))
        self.assertEqual(PaymentOrderOut.objects.get().payment_number, "000123/Б")

    def test_reducing_payment_below_unchanged_rows_is_rejected(self):
        payment = self.create_payment("100.00")
        item = PaymentOutItem.objects.create(payment=payment, invoice=self.invoice, amount="100")
        payment.amount = Decimal("50")
        forms = self.formset(payment, [
            {"id": item.pk, "invoice": self.invoice.pk, "amount": "100"},
        ])
        self.assertFalse(forms.is_valid())
        self.assertIn("больше суммы платежа", str(forms.non_form_errors()))

    def test_invoice_options_include_filter_metadata(self):
        html = str(PaymentOutItemInlineForm()["invoice"])
        self.assertIn(f'data-supplier-id="{self.supplier.pk}"', html)
        self.assertIn(f'data-organization-id="{self.organization.pk}"', html)


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
        cls.organization = Organization.objects.create(name="Тестовая организация")
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

    def create_supplier_price(self, price, applied_at):
        price_list = SupplierPriceList.objects.create(
            supplier=self.supplier,
            is_applied=True,
            dt_applied=applied_at,
        )
        SupplierPriceItem.objects.create(
            document=price_list,
            product=self.product,
            price=Money(price, "UAH"),
            wholesale_price=Money(price, "UAH"),
        )

    def test_uses_latest_supplier_price_before_order_application_date(self):
        self.create_supplier_price("80.00", datetime(2025, 1, 1, tzinfo=UTC))
        self.create_supplier_price("120.00", datetime(2025, 1, 3, tzinfo=UTC))
        order = PurchaseOrder.objects.create(
            supplier=self.supplier,
            organization=self.organization,
            is_applied=True,
            dt_applied=datetime(2025, 1, 2, tzinfo=UTC),
        )

        response = self.client.get(
            reverse("get_latest_price"),
            {"product_id": self.product.pk, "purchase_order_id": order.pk},
        )

        self.assertEqual(response.json()["price"], "80.00")

    def test_uses_latest_supplier_price_when_order_has_no_application_date(self):
        self.create_supplier_price("120.00", datetime(2025, 1, 3, tzinfo=UTC))
        order = PurchaseOrder.objects.create(
            supplier=self.supplier,
            organization=self.organization,
        )

        response = self.client.get(
            reverse("get_latest_price"),
            {"product_id": self.product.pk, "purchase_order_id": order.pk},
        )

        self.assertEqual(response.json()["price"], "100.00")


class RetailPriceAjaxDateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="retail-admin", password="test-password"
        )
        cls.organization = Organization.objects.create(name="Розничная организация")
        cls.customer = Contractor.objects.create(
            last_name="Розничный покупатель", is_customer=True
        )
        cls.store = RetailStore.objects.create(name="Тестовый магазин")
        cls.product = Product.objects.create(name="Розничный товар", sku="retail-product")

    def setUp(self):
        self.client.force_login(self.user)

    def create_retail_price(self, price, applied_at):
        price_list = RetailPriceList.objects.create(
            retail_store=self.store,
            organization=self.organization,
            is_applied=True,
            dt_applied=applied_at,
        )
        RetailPriceItem.objects.create(
            document=price_list,
            product=self.product,
            price=Money(price, "UAH"),
        )

    def test_uses_latest_retail_price_before_order_application_date(self):
        self.create_retail_price("150.00", datetime(2025, 1, 1, tzinfo=UTC))
        self.create_retail_price("250.00", datetime(2025, 1, 3, tzinfo=UTC))
        order = CustomerOrder.objects.create(
            customer=self.customer,
            retail_store=self.store,
            organization=self.organization,
            is_applied=True,
            dt_applied=datetime(2025, 1, 2, tzinfo=UTC),
        )

        response = self.client.get(
            reverse("get_latest_retail_price"),
            {
                "product_id": self.product.pk,
                "retail_store_id": self.store.pk,
                "customer_order_id": order.pk,
            },
        )

        self.assertEqual(response.json()["price"], "150.00")

    def test_uses_latest_retail_price_when_order_has_no_application_date(self):
        self.create_retail_price("250.00", datetime(2025, 1, 3, tzinfo=UTC))
        order = CustomerOrder.objects.create(
            customer=self.customer,
            retail_store=self.store,
            organization=self.organization,
        )

        response = self.client.get(
            reverse("get_latest_retail_price"),
            {
                "product_id": self.product.pk,
                "retail_store_id": self.store.pk,
                "customer_order_id": order.pk,
            },
        )

        self.assertEqual(response.json()["price"], "250.00")
