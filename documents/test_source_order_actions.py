from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from catalogs.models import Contractor, Organization, Product, Warehouse
from documents.models import GoodsReceipt, GoodsReceiptItem, InvoiceItem, OrderItem, PurchaseInvoice, PurchaseOrder


class SourceOrderActionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser("source-order-admin", password="test")
        cls.organization = Organization.objects.create(name="Организация основания")
        cls.supplier = Contractor.objects.create(last_name="Поставщик основания", is_supplier=True)
        cls.product = Product.objects.create(name="Товар основания", sku="source-order")
        cls.warehouse = Warehouse.objects.create(name="Склад основания", is_virtual=True)
        cls.order = PurchaseOrder.objects.create(supplier=cls.supplier, organization=cls.organization, is_applied=True)
        cls.item = OrderItem.objects.create(purchase_order=cls.order, organization=cls.organization,
                                           product=cls.product, warehouse=cls.warehouse, quantity=10, purchase_price=100)

    def setUp(self):
        self.enterContext(translation.override("ru"))
        self.client.force_login(self.user)

    def action_url(self, kind):
        action = "create_supplier_invoice" if kind == "invoice" else "create_goods_receipt"
        return reverse(f"admin:documents_purchaseorder_{action}", args=[self.order.pk])

    def test_menu_is_visible_on_posted_order(self):
        response = self.client.get(reverse("admin:documents_purchaseorder_change", args=[self.order.pk]))
        self.assertContains(response, "Создать на основании")
        self.assertContains(response, self.action_url("invoice"))
        self.assertContains(response, self.action_url("receipt"))

    def test_previews_are_filled_but_do_not_write_documents(self):
        for kind in ("invoice", "receipt"):
            with self.subTest(kind=kind):
                response = self.client.get(self.action_url(kind), follow=True)
                self.assertEqual(response.status_code, 200)
                form = response.context["adminform"].form
                self.assertEqual(form.initial["supplier"], self.supplier.pk)
                self.assertEqual(form.initial["organization"], self.organization.pk)
                self.assertEqual(form.initial["orders"], [self.order.pk])
                formset = response.context["inline_admin_formsets"][0].formset
                self.assertEqual(len(formset.forms), 1)
                self.assertEqual(formset.forms[0].initial["order_item"], self.item.pk)
                self.assertEqual(formset.forms[0].initial["quantity"], Decimal("10"))
                self.assertContains(response, "Строка №1")
        self.assertFalse(PurchaseInvoice.objects.exists())
        self.assertFalse(GoodsReceipt.objects.exists())

    def test_saving_prefilled_forms_preserves_links_and_edited_quantity(self):
        for kind, model in (("invoice", PurchaseInvoice), ("receipt", GoodsReceipt)):
            with self.subTest(kind=kind):
                redirect = self.client.get(self.action_url(kind))
                data = {
                    "supplier": self.supplier.pk, "organization": self.organization.pk,
                    "orders": [self.order.pk], "warehouse": self.warehouse.pk,
                    "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "0",
                    "items-0-order_item": self.item.pk, "items-0-quantity": "6",
                    "items-0-sort_order": "0", "_save": "Save",
                }
                response = self.client.post(redirect.url, data)
                if response.status_code != 302:
                    self.fail(str(response.context["adminform"].form.errors) + str(response.context["inline_admin_formsets"][0].formset.errors))
                obj = model.objects.get()
                self.assertFalse(obj.is_applied)
                self.assertEqual(list(obj.orders.values_list("pk", flat=True)), [self.order.pk])
                self.assertEqual(obj.items.get().quantity, Decimal("6"))
                self.assertEqual(obj.items.get().order_item_id, self.item.pk)

    def test_preview_uses_remaining_quantities(self):
        invoice = PurchaseInvoice.objects.create(supplier=self.supplier, organization=self.organization, is_applied=True)
        InvoiceItem.objects.create(invoice=invoice, order_item=self.item, quantity=6)
        receipt = GoodsReceipt.objects.create(supplier=self.supplier, organization=self.organization,
                                             warehouse=self.warehouse, is_applied=True)
        receipt.orders.add(self.order)
        GoodsReceiptItem.objects.create(receipt=receipt, order_item=self.item, quantity=3)
        for kind, remaining in (("invoice", 4), ("receipt", 7)):
            response = self.client.get(self.action_url(kind), follow=True)
            rows = response.context["inline_admin_formsets"][0].formset.forms
            self.assertEqual(rows[0].initial["quantity"], Decimal(remaining))

    def test_draft_order_actions_are_hidden_and_direct_url_forbidden(self):
        self.order.is_applied = False
        self.order.save()
        response = self.client.get(reverse("admin:documents_purchaseorder_change", args=[self.order.pk]))
        self.assertNotContains(response, "Создать на основании")
        self.assertEqual(self.client.get(self.action_url("receipt")).status_code, 403)
        url = reverse("admin:documents_goodsreceipt_add") + f"?from_order={self.order.pk}"
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_multiple_organizations_require_selection_before_filling(self):
        self.order.organization = None
        self.order.save()
        other = Organization.objects.create(name="Другая организация основания")
        other_item = OrderItem.objects.create(purchase_order=self.order, organization=other,
                                              product=self.product, warehouse=self.warehouse, quantity=3, purchase_price=100)
        for kind in ("invoice", "receipt"):
            chooser = self.client.get(self.action_url(kind), HTTP_HX_REQUEST="true")
            self.assertContains(chooser, "Выберите организацию")
            self.assertEqual(
                set(chooser.context["form"].fields["organization"].queryset),
                {self.organization, other},
            )
            invalid = self.client.post(self.action_url(kind), {
                "organization": Organization.objects.create(name=f"Чужая {kind}").pk,
                "_form_submitted": "on",
            }, HTTP_HX_REQUEST="true")
            self.assertTrue(invalid.context["form"].errors)
            redirect = self.client.post(self.action_url(kind), {
                "organization": other.pk, "_form_submitted": "on",
            }, HTTP_HX_REQUEST="true")
            response = self.client.get(redirect.headers["HX-Redirect"])
            rows = response.context["inline_admin_formsets"][0].formset.forms
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].initial["order_item"], other_item.pk)
        self.assertFalse(GoodsReceipt.objects.exists())
        self.assertFalse(PurchaseInvoice.objects.exists())

    def test_add_permission_is_required(self):
        staff = get_user_model().objects.create_user("source-viewer", password="test", is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename="view_purchaseorder"))
        self.client.force_login(staff)
        for kind in ("invoice", "receipt"):
            self.assertEqual(self.client.get(self.action_url(kind)).status_code, 403)

    def test_unrelated_organization_cannot_be_injected(self):
        other = Organization.objects.create(name="Чужая")
        redirect = self.client.get(self.action_url("invoice"))
        self.assertEqual(self.client.get(redirect.url + f"&organization={other.pk}").status_code, 403)

    def test_unchanged_prefilled_row_is_saved(self):
        redirect = self.client.get(self.action_url("receipt"))
        self.client.get(redirect.url)
        response = self.client.post(redirect.url, {
            "supplier": self.supplier.pk, "organization": self.organization.pk,
            "orders": [self.order.pk], "warehouse": self.warehouse.pk,
            "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "0",
            "items-0-order_item": self.item.pk, "items-0-quantity": "10",
            "items-0-sort_order": "0", "_save": "Save",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(GoodsReceiptItem.objects.get().quantity, Decimal("10"))

    def test_balance_is_rechecked_after_preview(self):
        redirect = self.client.get(self.action_url("invoice"))
        self.client.get(redirect.url)
        existing = PurchaseInvoice.objects.create(supplier=self.supplier, organization=self.organization, is_applied=True)
        InvoiceItem.objects.create(invoice=existing, order_item=self.item, quantity=6)
        response = self.client.post(redirect.url, {
            "supplier": self.supplier.pk, "organization": self.organization.pk,
            "orders": [self.order.pk], "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "0",
            "items-0-order_item": self.item.pk, "items-0-quantity": "10", "items-0-sort_order": "0",
            "_save": "Save",
        })
        self.assertContains(response, "Доступно для счёта")
        self.assertEqual(PurchaseInvoice.objects.count(), 1)


    def test_single_organization_htmx_skips_dialog(self):
        for kind in ("invoice", "receipt"):
            response = self.client.get(self.action_url(kind), HTTP_HX_REQUEST="true")
            self.assertIn("HX-Redirect", response.headers)
            self.assertNotContains(response, 'id="dialog"')


    def test_header_organization_does_not_hide_other_line_organizations(self):
        other = Organization.objects.create(name="Организация строки")
        item = OrderItem.objects.create(
            purchase_order=self.order, organization=other, product=self.product,
            warehouse=self.warehouse, quantity=3, purchase_price=100,
        )
        for kind in ("invoice", "receipt"):
            chooser = self.client.get(self.action_url(kind), HTTP_HX_REQUEST="true")
            self.assertContains(chooser, "Выберите организацию")
            redirect = self.client.post(self.action_url(kind), {
                "organization": other.pk, "_form_submitted": "on",
            }, HTTP_HX_REQUEST="true")
            response = self.client.get(redirect.headers["HX-Redirect"])
            rows = response.context["inline_admin_formsets"][0].formset.forms
            self.assertEqual([row.initial["order_item"] for row in rows], [item.pk])
        receipt = GoodsReceipt(supplier=self.supplier, organization=other, warehouse=self.warehouse)
        receipt._selected_order_ids = [self.order.pk]
        receipt.clean()
        GoodsReceiptItem(receipt=receipt, order_item=item, quantity=Decimal("3")).clean()
