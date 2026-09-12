from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import translation

from catalogs.models import Contractor, ContractorBankAccount, Organization, Product, Warehouse
from documents.admin import PaymentOutItemInlineForm, PurchaseOrderItemInline
from documents.invoices import with_invoice_balance
from documents.models import InvoiceItem, OrderItem, PaymentOrderOut, PaymentOutItem, PurchaseInvoice, PurchaseOrder


class PartialInvoiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser("invoice-admin", password="test")
        cls.organization = Organization.objects.create(name="Организация счетов")
        cls.supplier = Contractor.objects.create(last_name="Поставщик счетов", is_supplier=True)
        account = ContractorBankAccount.objects.create(contractor=cls.supplier, bank_name="Банк", iban="UA111111111111111111111111111")
        cls.supplier.primary_account = account
        cls.supplier.save()
        cls.warehouse = Warehouse.objects.create(name="Склад счетов", is_virtual=True)
        cls.product = Product.objects.create(name="Товар для счетов", sku="invoice-partial")
        cls.order = PurchaseOrder.objects.create(supplier=cls.supplier, organization=cls.organization, is_applied=True)
        cls.item = OrderItem.objects.create(
            purchase_order=cls.order, organization=cls.organization, warehouse=cls.warehouse,
            product=cls.product, quantity=10, purchase_price=Decimal("100"),
        )

    def setUp(self):
        self.enterContext(translation.override("ru"))
        self.client.force_login(self.user)

    def post_invoice(self, quantity=None, *, applied=False, fill=False, invoice=None, **overrides):
        data = {
            "supplier": self.supplier.pk, "organization": self.organization.pk,
            "orders": [self.order.pk], "items-TOTAL_FORMS": "0", "items-INITIAL_FORMS": "0",
            "_save": "Save",
        }
        if applied:
            data["is_applied"] = "on"
        if fill:
            data["fill_from_orders"] = "on"
        if quantity is not None:
            data.update({"items-TOTAL_FORMS": "1", "items-0-order_item": self.item.pk,
                         "items-0-quantity": str(quantity), "items-0-sort_order": "0"})
        if invoice:
            data["items-INITIAL_FORMS"] = str(invoice.items.count())
            if quantity is not None:
                data["items-0-id"] = invoice.items.get().pk
        data.update(overrides)
        url = reverse("admin:documents_purchaseinvoice_change", args=[invoice.pk]) if invoice else reverse("admin:documents_purchaseinvoice_add")
        return self.client.post(url, data)

    def assert_saved(self, response):
        if response.status_code != 302:
            self.fail(str(response.context["adminform"].form.errors) + str([
                (inline.formset.errors, inline.formset.non_form_errors())
                for inline in response.context["inline_admin_formsets"]
            ]))

    def remaining(self):
        return with_invoice_balance(OrderItem.objects.all()).get(pk=self.item.pk).invoice_remaining

    def test_two_partial_invoices_share_one_order_line(self):
        self.assert_saved(self.post_invoice(6, applied=True))
        self.assertEqual(self.remaining(), Decimal("4"))
        self.assert_saved(self.post_invoice(fill=True, applied=True))
        self.assertEqual(PurchaseInvoice.objects.latest("pk").items.get().quantity, Decimal("4"))
        self.assertEqual(self.item.invoice_items.count(), 2)
        self.assertEqual(self.remaining(), Decimal("0"))

    def test_partial_amount_is_used_for_invoice_and_payment(self):
        self.assert_saved(self.post_invoice(6, applied=True))
        invoice = PurchaseInvoice.objects.get()
        request = RequestFactory().get("/")
        request.user = self.user
        annotated = admin.site._registry[PurchaseInvoice].get_queryset(request).get()
        self.assertEqual(annotated.calculated_total, Decimal("600"))
        self.assertEqual(annotated.calculated_quantity, Decimal("6"))
        choice = PaymentOutItemInlineForm().fields["invoice"].queryset.get()
        self.assertEqual(choice.calculated_total, Decimal("600"))
        payment = PaymentOrderOut.objects.create(
            organization=self.organization, contractor=self.supplier, amount=600, payment_number="1",
        )
        row = PaymentOutItem.objects.create(payment=payment, invoice=invoice)
        self.assertEqual(row.amount, Decimal("600"))

    def test_draft_does_not_reduce_remaining_and_posting_checks_balance(self):
        self.assert_saved(self.post_invoice(10))
        draft = PurchaseInvoice.objects.get()
        self.assertEqual(self.remaining(), Decimal("10"))
        self.assert_saved(self.post_invoice(6, applied=True))
        response = self.post_invoice(10, invoice=draft, applied=True)
        self.assertContains(response, "Доступно для счёта")
        draft.refresh_from_db()
        self.assertFalse(draft.is_applied)

    def test_edit_excludes_current_invoice_and_unposting_releases_quantity(self):
        self.assert_saved(self.post_invoice(6, applied=True))
        invoice = PurchaseInvoice.objects.get()
        self.assert_saved(self.post_invoice(6, invoice=invoice, applied=True))
        self.assertEqual(self.remaining(), Decimal("4"))
        self.assert_saved(self.post_invoice(6, invoice=invoice))
        self.assertEqual(self.remaining(), Decimal("10"))

    def test_blank_quantity_fills_remaining(self):
        self.assert_saved(self.post_invoice(6, applied=True))
        self.assert_saved(self.post_invoice(""))
        self.assertEqual(PurchaseInvoice.objects.latest("pk").items.get().quantity, Decimal("4"))

    def test_excess_negative_zero_and_fractional_piece_quantity_rejected(self):
        for quantity in (11, 0, -1, "1.5"):
            with self.subTest(quantity=quantity):
                response = self.post_invoice(quantity)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(PurchaseInvoice.objects.exists())

    def test_foreign_organization_is_rejected(self):
        other = Organization.objects.create(name="Другая организация")
        response = self.post_invoice(1, organization=other.pk)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PurchaseInvoice.objects.exists())

    def test_duplicate_line_in_one_invoice_is_rejected(self):
        response = self.post_invoice(4, **{
            "items-TOTAL_FORMS": "2", "items-1-order_item": self.item.pk, "items-1-quantity": "4",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PurchaseInvoice.objects.exists())

    def test_deleted_line_is_not_restored_by_fill(self):
        self.assert_saved(self.post_invoice(6))
        invoice = PurchaseInvoice.objects.get()
        self.assert_saved(self.post_invoice(6, invoice=invoice, fill=True, **{"items-0-DELETE": "on"}))
        self.assertFalse(invoice.items.exists())

    def test_order_lists_both_invoice_links(self):
        self.assert_saved(self.post_invoice(6, applied=True))
        self.assert_saved(self.post_invoice(4, applied=True))
        links = str(PurchaseOrderItemInline(PurchaseOrder, admin.site).get_invoice_link(self.item))
        for invoice in PurchaseInvoice.objects.all():
            self.assertIn(reverse("admin:documents_purchaseinvoice_change", args=[invoice.pk]), links)

    def test_units_control_quantity_widgets_in_invoices_and_receipts(self):
        from catalogs.models import MeasurementUnit
        from documents.admin import GoodsReceiptItemForm, PurchaseInvoiceItemInlineForm
        from documents.models import GoodsReceiptItem

        for places, quantity, expected in ((0, "2.000000", "2"), (3, "1.250000", "1.250")):
            unit = MeasurementUnit.objects.create(
                code=f"test-unit-{places}", name="Единица", symbol=f"ед{places}", decimal_places=places,
            )
            self.product.unit = unit
            self.product.save()
            for model, form_class in ((InvoiceItem, PurchaseInvoiceItemInlineForm), (GoodsReceiptItem, GoodsReceiptItemForm)):
                with self.subTest(places=places, model=model.__name__):
                    instance = model(pk=999, order_item_id=self.item.pk, quantity=Decimal(quantity))
                    form = form_class(instance=instance)
                    self.assertEqual(form.fields["quantity"].widget.attrs["step"], "1" if places == 0 else "0.001")
                    self.assertEqual(form.initial["quantity"], expected)
                    html = str(form["order_item"])
                    self.assertIn(f'data-quantity-decimal-places="{places}"', html)
                    self.assertIn(f'data-unit-symbol="ед{places}"', html)

    def test_invalid_quantity_is_not_rounded_in_forms(self):
        from documents.admin import GoodsReceiptItemForm, PurchaseInvoiceItemInlineForm
        from documents.models import GoodsReceiptItem

        for model, form_class in ((InvoiceItem, PurchaseInvoiceItemInlineForm), (GoodsReceiptItem, GoodsReceiptItemForm)):
            with self.subTest(model=model.__name__):
                instance = model(pk=999, order_item_id=self.item.pk, quantity=Decimal("1.25"))
                form = form_class(instance=instance)
                self.assertEqual(Decimal(form.initial["quantity"]), Decimal("1.25"))
