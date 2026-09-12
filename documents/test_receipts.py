from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from catalogs.models import Contractor, Organization, Product, Warehouse
from documents.models import GoodsReceipt, GoodsReceiptItem, OrderItem, PurchaseOrder
from documents.receipts import receipt_order_items


class GoodsReceiptTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser("receipt-admin", password="test")
        cls.organization = Organization.objects.create(name="Приёмка")
        cls.other_org = Organization.objects.create(name="Другая")
        cls.supplier = Contractor.objects.create(last_name="Поставщик", is_supplier=True)
        cls.warehouse = Warehouse.objects.create(name="Склад", is_virtual=False)
        cls.product = Product.objects.create(name="Товар", sku="receipt-test")
        cls.order = PurchaseOrder.objects.create(
            supplier=cls.supplier, organization=cls.organization, is_applied=True,
        )
        cls.item = OrderItem.objects.create(
            purchase_order=cls.order, organization=cls.organization,
            product=cls.product, warehouse=cls.warehouse,
            quantity=10, purchase_price=Decimal("12.50"),
        )

    def setUp(self):
        self.enterContext(translation.override("ru"))
        self.client.force_login(self.user)

    def post_receipt(self, *, quantity=None, applied=False, fill=False, receipt=None, **overrides):
        data = {
            "organization": self.organization.pk, "purchase_order": self.order.pk,
            "warehouse": self.warehouse.pk,
            "items-TOTAL_FORMS": "0", "items-INITIAL_FORMS": "0", "_save": "Save",
        }
        if applied:
            data["is_applied"] = "on"
        if fill:
            data["fill_from_order"] = "on"
        if quantity is not None:
            data.update({
                "items-TOTAL_FORMS": "1", "items-0-order_item": self.item.pk,
                "items-0-quantity": str(quantity),
                "items-0-sort_order": "0",
            })
        if receipt:
            data["items-INITIAL_FORMS"] = str(receipt.items.count())
            if quantity is not None:
                data["items-0-id"] = receipt.items.get().pk
        data.update(overrides)
        url = reverse("admin:documents_goodsreceipt_change", args=[receipt.pk]) if receipt else reverse("admin:documents_goodsreceipt_add")
        return self.client.post(url, data)

    def assert_saved(self, response):
        if response.status_code != 302:
            errors = response.context["adminform"].form.errors
            inline_errors = [(i.formset.errors, i.formset.non_form_errors()) for i in response.context["inline_admin_formsets"]]
            self.fail(f"Receipt not saved: {errors}; {inline_errors}")

    def balance(self, receipt=None):
        context = receipt or GoodsReceipt(purchase_order=self.order, organization=self.organization)
        return receipt_order_items(context).get(pk=self.item.pk).remaining_quantity

    def test_partial_receipts_and_autofill_remaining(self):
        self.assert_saved(self.post_receipt(quantity=6, applied=True))
        self.assertEqual(self.balance(), Decimal("4"))
        self.assert_saved(self.post_receipt(fill=True, applied=True))
        second = GoodsReceipt.objects.first()
        self.assertEqual(second.items.get().quantity, Decimal("4"))
        self.assertEqual(second.items.get().total_price, Decimal("50.00"))
        self.assertEqual(self.balance(), Decimal("0"))

    def test_drafts_do_not_reduce_balance_and_overreceipt_is_rejected_on_posting(self):
        self.assert_saved(self.post_receipt(quantity=10))
        draft = GoodsReceipt.objects.get()
        self.assertEqual(self.balance(), Decimal("10"))
        self.assert_saved(self.post_receipt(quantity=6, applied=True))
        response = self.post_receipt(quantity=10, applied=True, receipt=draft)
        self.assertContains(response, "Доступно к поступлению")
        draft.refresh_from_db()
        self.assertFalse(draft.is_applied)
        self.assertEqual(self.balance(), Decimal("4"))

    def test_edit_excludes_self_and_unposting_restores_balance(self):
        self.assert_saved(self.post_receipt(quantity=6, applied=True))
        receipt = GoodsReceipt.objects.get()
        self.assert_saved(self.post_receipt(quantity=6, applied=True, receipt=receipt))
        self.assertEqual(self.balance(), Decimal("4"))
        self.assert_saved(self.post_receipt(quantity=6, receipt=receipt))
        self.assertEqual(self.balance(), Decimal("10"))

    def test_draft_receipt_total_uses_order_price(self):
        self.assert_saved(self.post_receipt(fill=True))
        self.item.purchase_price = Decimal("99")
        self.item.save()
        row = GoodsReceiptItem.objects.select_related("order_item").get()
        self.assertEqual(row.total_price, Decimal("990.00"))

    def test_empty_receipt_cannot_be_posted(self):
        response = self.post_receipt(applied=True)
        self.assertContains(response, "Нельзя провести поступление без позиций")
        self.assertFalse(GoodsReceipt.objects.exists())

    def test_excess_quantity_rejected_without_writing(self):
        response = self.post_receipt(quantity=11, applied=True)
        self.assertContains(response, "Доступно к поступлению")
        self.assertFalse(GoodsReceipt.objects.exists())

    def test_header_organization_mismatch_rejected(self):
        response = self.post_receipt(fill=True, organization=self.other_org.pk)
        self.assertContains(response, "Организация поступления должна совпадать")
        self.assertFalse(GoodsReceipt.objects.exists())

    def test_foreign_item_organization_excluded_and_rejected(self):
        OrderItem.objects.filter(pk=self.item.pk).update(organization=self.other_org)
        response = self.post_receipt(quantity=1)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(GoodsReceipt.objects.exists())
        self.assert_saved(self.post_receipt(fill=True))
        self.assertFalse(GoodsReceiptItem.objects.exists())

    def test_draft_order_is_rejected(self):
        PurchaseOrder.objects.filter(pk=self.order.pk).update(is_applied=False)
        response = self.post_receipt(fill=True)
        self.assertEqual(response.status_code, 200, response.content.decode())
        self.assertFalse(GoodsReceipt.objects.exists())

    def test_quantity_receipt_does_not_require_price(self):
        self.item.purchase_price = None
        self.item.save()
        self.assert_saved(self.post_receipt(fill=True))
        self.assertIsNone(GoodsReceiptItem.objects.get().total_price)

    def test_zero_negative_and_fractional_piece_quantities_rejected(self):
        for quantity in ("0", "-1", "1.5"):
            with self.subTest(quantity=quantity):
                response = self.post_receipt(quantity=quantity)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(GoodsReceipt.objects.exists())

    def test_repeated_fill_preserves_existing_partial_quantity(self):
        self.assert_saved(self.post_receipt(quantity=6))
        receipt = GoodsReceipt.objects.get()
        self.assert_saved(self.post_receipt(quantity=6, receipt=receipt, fill=True))
        self.assertEqual(receipt.items.get().quantity, Decimal("6"))

    def test_line_must_belong_to_receipt_order(self):
        other = PurchaseOrder.objects.create(supplier=self.supplier, organization=self.organization, is_applied=True)
        receipt = GoodsReceipt(purchase_order=other, organization=self.organization, warehouse=self.warehouse)
        row = GoodsReceiptItem(receipt=receipt, order_item=self.item, quantity=Decimal("1"))
        with self.assertRaises(ValidationError):
            row.full_clean(exclude=("receipt",))

    def test_deleted_row_is_not_restored_by_fill(self):
        self.assert_saved(self.post_receipt(quantity=6))
        receipt = GoodsReceipt.objects.get()
        self.assert_saved(self.post_receipt(
            quantity=6, receipt=receipt, fill=True, **{"items-0-DELETE": "on"},
        ))
        self.assertFalse(receipt.items.exists())
        self.assertEqual(self.balance(), Decimal("10"))

    def test_duplicate_order_line_is_rejected(self):
        response = self.post_receipt(quantity=4, **{
            "items-TOTAL_FORMS": "2", "items-1-order_item": self.item.pk,
            "items-1-quantity": "4",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(GoodsReceipt.objects.exists())

    def test_zero_order_price(self):
        self.item.purchase_price = Decimal("0")
        self.item.save()
        self.assert_saved(self.post_receipt(quantity=1))
        self.assertEqual(GoodsReceiptItem.objects.get().total_price, Decimal("0"))

    def test_completed_order_does_not_fill_another_receipt(self):
        self.assert_saved(self.post_receipt(fill=True, applied=True))
        response = self.post_receipt(fill=True, applied=True)
        self.assertContains(response, "Нельзя провести поступление без позиций")
        self.assertEqual(GoodsReceipt.objects.count(), 1)

    def test_order_inline_shows_received_and_remaining(self):
        from django.contrib import admin
        from django.test import RequestFactory
        from documents.admin import PurchaseOrderItemInline

        self.assert_saved(self.post_receipt(quantity=6, applied=True))
        inline = PurchaseOrderItemInline(PurchaseOrder, admin.site)
        request = RequestFactory().get("/")
        request.user = self.user
        item = inline.get_queryset(request).get(pk=self.item.pk)
        self.assertEqual(inline.received_quantity(item), Decimal("6"))
        self.assertEqual(inline.remaining_quantity(item), Decimal("4"))


    def test_applied_receipt_locks_source_line(self):
        self.assert_saved(self.post_receipt(quantity=6, applied=True))
        changes = {
            "purchase_price": Decimal("99"), "quantity": Decimal("11"),
            "organization_id": self.other_org.pk, "purchase_order_id": None,
            "product_id": None, "warehouse_id": None,
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                item = OrderItem.objects.get(pk=self.item.pk)
                setattr(item, field, value)
                with self.assertRaisesMessage(ValidationError, "проведённым поступлением"):
                    item.save()
        self.item.refresh_from_db()
        self.assertEqual(self.item.purchase_price, Decimal("12.50"))

    def test_applied_receipt_locks_order_unposting_and_reposting(self):
        self.assert_saved(self.post_receipt(quantity=6, applied=True))
        for field, value in (
            ("is_applied", False), ("to_remove", True),
            ("organization_id", self.other_org.pk), ("supplier_id", None),
            ("_force_current_date", True), ("dt_applied", None),
        ):
            with self.subTest(field=field):
                order = PurchaseOrder.objects.get(pk=self.order.pk)
                setattr(order, field, value)
                with self.assertRaisesMessage(ValidationError, "снимите проведение"):
                    order.save()

    def test_order_form_reports_lock_as_validation_error(self):
        from documents.admin import PurchaseOrderForm

        self.assert_saved(self.post_receipt(quantity=6, applied=True))
        order = PurchaseOrder.objects.get(pk=self.order.pk)
        form = PurchaseOrderForm(instance=order, data={
            "supplier": self.supplier.pk, "organization": self.organization.pk,
            "is_applied": "on", "force_current_date": "on",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("снимите проведение", str(form.non_field_errors()))

    def test_unposting_receipt_unlocks_order_and_price(self):
        self.assert_saved(self.post_receipt(quantity=6, applied=True))
        receipt = GoodsReceipt.objects.get()
        self.assert_saved(self.post_receipt(quantity=6, receipt=receipt))
        order = PurchaseOrder.objects.get(pk=self.order.pk)
        order.is_applied = False
        order.save()
        self.item.purchase_price = Decimal("20")
        self.item.save()
        self.assertEqual(GoodsReceiptItem.objects.get().total_price, Decimal("120"))

    def test_receipt_protects_source_line_from_deletion(self):
        from django.db.models.deletion import ProtectedError

        self.assert_saved(self.post_receipt(quantity=6, applied=True))
        with self.assertRaises(ProtectedError):
            OrderItem.objects.filter(pk=self.item.pk).delete()
        with self.assertRaises(ProtectedError):
            self.order.delete()

    def test_receipt_admin_total_uses_received_quantity(self):
        from django.contrib import admin
        from django.test import RequestFactory

        self.assert_saved(self.post_receipt(quantity=6, applied=True))
        request = RequestFactory().get("/")
        request.user = self.user
        receipt = admin.site._registry[GoodsReceipt].get_queryset(request).get()
        self.assertEqual(receipt.calculated_total, Decimal("75.00"))

    def test_order_position_numbers_are_rendered_without_javascript(self):
        from django.contrib import admin
        from django.test import RequestFactory
        from documents.admin import PurchaseOrderItemInline

        second = OrderItem.objects.create(
            purchase_order=self.order, organization=self.organization,
            product=self.product, warehouse=self.warehouse,
            quantity=1, purchase_price=10, sort_order_purchase=8,
        )
        third = OrderItem.objects.create(
            purchase_order=self.order, organization=self.organization,
            product=self.product, warehouse=self.warehouse,
            quantity=1, purchase_price=10, sort_order_purchase=3,
        )
        inline = PurchaseOrderItemInline(PurchaseOrder, admin.site)
        request = RequestFactory().get("/")
        request.user = self.user
        rows = list(inline.get_queryset(request).filter(purchase_order=self.order))
        self.assertEqual([row.pk for row in rows], [self.item.pk, third.pk, second.pk])
        self.assertEqual([row.calculated_position_number for row in rows], [1, 2, 3])
        for number, row in enumerate(rows, 1):
            self.assertIn(f'>{number}</span>', str(inline.position_number(row)))
