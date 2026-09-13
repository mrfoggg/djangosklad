from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from catalogs.models import Contractor, Organization, Product, RetailStore, Warehouse
from documents.models import SalesDocument, SalesDocumentItem, CustomerOrder, OrderItem


class SalesDocumentTests(TestCase):
    def setUp(self):
        self.enterContext(translation.override("ru"))
        self.organization = Organization.objects.create(name="Продажи")
        self.customer = Contractor.objects.create(last_name="Покупатель", is_customer=True)
        self.product = Product.objects.create(name="Товар", sku="sales-doc-test")
        self.order = CustomerOrder.objects.create(
            customer=self.customer, organization=self.organization, is_applied=True,
            retail_store=RetailStore.objects.create(name="Продажи"),
        )
        self.item = OrderItem.objects.create(
            customer_order=self.order, organization=self.organization, product=self.product,
            warehouse=Warehouse.objects.create(name="Продажи", is_virtual=True),
            quantity=Decimal("10"), customer_price=Decimal("123.45"),
        )
        self.document = SalesDocument.objects.create(organization=self.organization, customer=self.customer)
        self.document.orders.add(self.order)

    def test_line_total_and_quantity_precision(self):
        line = SalesDocumentItem(document=self.document, order_item=self.item, quantity=Decimal("2"))
        line.full_clean()
        line.save()
        line.refresh_from_db()
        self.assertEqual(line.total_price, Decimal("246.90"))
        line.quantity = Decimal("1.5")
        with self.assertRaises(ValidationError):
            line.full_clean()
        line.quantity = Decimal("0")
        with self.assertRaises(ValidationError):
            line.full_clean()

    def test_admin_creates_document_with_lines_and_blocks_empty_posting(self):
        self.client.force_login(get_user_model().objects.create_superuser("sales-admin", password="test"))
        url = reverse("admin:documents_salesdocument_add")
        self.assertEqual(self.client.get(url).status_code, 200)
        data = {
            "organization": self.organization.pk, "customer": self.customer.pk, "orders": [self.order.pk],
            "delivery_note_number": "RN-1", "delivery_note_date": "2026-09-13",
            "items-TOTAL_FORMS": "0", "items-INITIAL_FORMS": "0",
            "is_applied": "on", "_save": "Save",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["inline_admin_formsets"][0].formset.non_form_errors())
        data.update({
            "items-TOTAL_FORMS": "1", "items-0-order_item": self.item.pk,
            "items-0-quantity": "2", "items-0-sort_order": "0",
        })
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        document = SalesDocument.objects.get(delivery_note_number="RN-1")
        self.assertTrue(document.is_applied)
        self.assertEqual(document.items.get().total_price, Decimal("246.90"))


    def test_partial_sales_balance_excludes_drafts_and_current_document(self):
        from documents.sales import sales_order_items
        self.document.is_applied = True
        self.document.save()
        SalesDocumentItem.objects.create(document=self.document, order_item=self.item, quantity=4)
        draft = SalesDocument.objects.create(customer=self.customer, organization=self.organization)
        draft.orders.add(self.order)
        SalesDocumentItem.objects.create(document=draft, order_item=self.item, quantity=2)
        self.assertEqual(sales_order_items(draft).get().remaining_quantity, Decimal("6"))
        self.assertEqual(sales_order_items(self.document).get().remaining_quantity, Decimal("10"))

    def test_fill_multiple_orders_and_reject_excess_quantity(self):
        self.client.force_login(get_user_model().objects.create_superuser("sales-fill", password="test"))
        other = CustomerOrder.objects.create(
            customer=self.customer, organization=self.organization, is_applied=True,
            retail_store=self.order.retail_store,
        )
        other_item = OrderItem.objects.create(
            customer_order=other, organization=self.organization, product=self.product,
            warehouse=self.item.warehouse, quantity=3, customer_price=50,
        )
        url = reverse("admin:documents_salesdocument_add")
        data = {
            "organization": self.organization.pk, "customer": self.customer.pk,
            "orders": [self.order.pk, other.pk], "fill_from_order": "on",
            "items-TOTAL_FORMS": "0", "items-INITIAL_FORMS": "0", "_save": "Save",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        filled = SalesDocument.objects.latest("pk")
        self.assertEqual(set(filled.items.values_list("order_item_id", flat=True)), {self.item.pk, other_item.pk})
        data.pop("fill_from_order")
        data.update({"items-TOTAL_FORMS": "1", "items-0-order_item": self.item.pk,
                     "items-0-quantity": "11", "items-0-sort_order": "0"})
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("quantity", response.context["inline_admin_formsets"][0].formset.errors[0])

    def test_wrong_customer_and_missing_order_are_rejected(self):
        self.document.customer = Contractor.objects.create(last_name="Чужой покупатель", is_customer=True)
        with self.assertRaises(ValidationError):
            self.document.clean()
        self.document.customer = self.customer
        self.document._selected_order_ids = []
        line = SalesDocumentItem(document=self.document, order_item=self.item, quantity=Decimal("1"))
        with self.assertRaises(ValidationError):
            line.clean()


    def test_posted_sale_locks_order_price(self):
        self.document.is_applied = True
        self.document.save()
        SalesDocumentItem.objects.create(document=self.document, order_item=self.item, quantity=1)
        self.item.customer_price = Decimal("200")
        with self.assertRaises(ValidationError):
            self.item.save()
        self.document.is_applied = False
        self.document.save()
        self.item.save()
        line = self.document.items.get()
        self.assertEqual(line.total_price, Decimal("200"))
