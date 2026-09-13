from decimal import Decimal
from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from catalogs.models import Contractor, DeliveryMethod, Organization, Product, RetailStore, Warehouse
from documents.models import CustomerOrder, OrderItem, SalesDocument, SalesDocumentItem, Shipment, ShipmentItem
from documents.shipments import shipment_sales_items


class ShipmentTests(TestCase):
    def setUp(self):
        self.enterContext(translation.override("ru"))
        self.client.force_login(get_user_model().objects.create_superuser("shipment-admin", password="test"))
        self.org = Organization.objects.create(name="Отгрузки")
        self.customer = Contractor.objects.create(last_name="Покупатель", is_customer=True)
        self.order = CustomerOrder.objects.create(
            organization=self.org, customer=self.customer, is_applied=True,
            retail_store=RetailStore.objects.create(name="Отгрузки"),
        )
        order_item = OrderItem.objects.create(
            customer_order=self.order, organization=self.org, quantity=10, customer_price=100,
            product=Product.objects.create(name="Товар", sku="shipment-product"),
            warehouse=Warehouse.objects.create(name="Отгрузки", is_virtual=True),
        )
        self.sale = SalesDocument.objects.create(organization=self.org, customer=self.customer, is_applied=True)
        self.sale.orders.add(self.order)
        self.item = SalesDocumentItem.objects.create(document=self.sale, order_item=order_item, quantity=10)
        self.carrier = DeliveryMethod.objects.get(name="Новая почта")
        self.pickup = DeliveryMethod.objects.get(name="Самовывоз")

    def shipment(self, method=None, **kwargs):
        return Shipment(
            organization=self.org, sales_document=self.sale, delivery_method=method or self.carrier, **kwargs,
        )

    def test_conditional_fields_render_and_new_carrier_has_metadata(self):
        new = DeliveryMethod.objects.create(name="Новый перевозчик")
        response = self.client.get(reverse("admin:documents_shipment_add"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-kind="carrier"')
        self.assertContains(response, 'data-kind="pickup"')
        self.assertContains(response, "dataset.kind")
        self.assertContains(response, new.name)
        self.assertContains(response, "handover_confirmed")

    def test_carrier_and_pickup_have_different_required_fields(self):
        carrier = self.shipment(is_applied=True, status="handed")
        with self.assertRaises(ValidationError) as error:
            carrier.clean()
        self.assertIn("tracking_number", error.exception.message_dict)
        self.assertIn("branch", error.exception.message_dict)
        self.assertNotIn("pickup_location", error.exception.message_dict)
        carrier.recipient_name = "Получатель"
        carrier.recipient_phone = "0501234567"
        carrier.city = "Киев"
        carrier.branch = "1"
        carrier.tracking_number = "TTN-1"
        carrier.shipment_date = date.today()
        carrier.full_clean()
        carrier.destination = "address"
        with self.assertRaises(ValidationError) as error:
            carrier.clean()
        self.assertIn("address", error.exception.message_dict)
        pickup = self.shipment(self.pickup, is_applied=True, status="delivered",
                               recipient_name="Получатель", shipment_date=date.today())
        with self.assertRaises(ValidationError) as error:
            pickup.clean()
        self.assertIn("handover_confirmed", error.exception.message_dict)
        self.assertNotIn("tracking_number", error.exception.message_dict)
        pickup.pickup_location = "Склад"
        pickup.received_by = "Получатель"
        pickup.handover_confirmed = True
        pickup.full_clean()

    def test_partial_balance_excludes_drafts_and_current_shipment(self):
        posted = self.shipment(is_applied=True, status="handed")
        posted.save()
        ShipmentItem.objects.create(shipment=posted, sales_item=self.item, quantity=4)
        draft = self.shipment()
        draft.save()
        ShipmentItem.objects.create(shipment=draft, sales_item=self.item, quantity=3)
        self.assertEqual(shipment_sales_items(draft).get().remaining_quantity, 6)
        choice = shipment_sales_items(posted).get()
        self.assertEqual(choice.remaining_quantity, 10)
        self.assertEqual(choice.displayed_remaining, 6)

    def test_create_from_sale_does_not_write_and_can_save_initial_rows(self):
        action = reverse("admin:documents_salesdocument_create_shipment", args=[self.sale.pk])
        redirect = self.client.get(action)
        page = self.client.get(redirect.url)
        self.assertEqual(page.status_code, 200)
        rows = page.context["inline_admin_formsets"][0].formset.forms
        self.assertEqual(rows[0].initial["sales_item"], self.item.pk)
        self.assertFalse(Shipment.objects.exists())
        response = self.client.post(redirect.url, self.data(quantity="4"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Shipment.objects.get().items.get().quantity, Decimal("4"))

    def data(self, quantity="4"):
        return {
            "organization": self.org.pk, "sales_document": self.sale.pk,
            "delivery_method": self.carrier.pk, "status": "preparing", "destination": "branch",
            "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "0", "items-0-sales_item": self.item.pk,
            "items-0-quantity": quantity, "items-0-sort_order": "0", "_save": "Save",
        }

    def test_excess_quantity_and_unrelated_realization_rejected(self):
        url = reverse("admin:documents_shipment_add")
        response = self.client.post(url, self.data(quantity="11"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("quantity", response.context["inline_admin_formsets"][0].formset.errors[0])
        wrong = self.shipment()
        wrong.organization = Organization.objects.create(name="Чужая")
        with self.assertRaises(ValidationError):
            wrong.clean()
        self.sale.is_applied = False
        self.sale.save()
        with self.assertRaises(ValidationError):
            self.shipment().clean()

    def test_fill_remaining_and_posting_requires_rows(self):
        data = self.data()
        data.update({"items-TOTAL_FORMS": "0", "fill_remaining": "on"})
        url = reverse("admin:documents_shipment_add")
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Shipment.objects.get().items.get().quantity, 10)
        data.pop("fill_remaining")
        data.update({
            "is_applied": "on", "status": "delivered", "delivery_method": self.pickup.pk,
            "recipient_name": "Получатель", "shipment_date": "2026-09-13",
            "pickup_location": "Склад", "received_by": "Получатель", "handover_confirmed": "on",
        })
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["inline_admin_formsets"][0].formset.non_form_errors())
        data["fill_remaining"] = "on"
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Shipment.objects.latest("pk").is_applied)

    def test_posted_shipment_locks_source_document_and_quantity(self):
        shipment = self.shipment(is_applied=True, status="handed")
        shipment.save()
        ShipmentItem.objects.create(shipment=shipment, sales_item=self.item, quantity=4)
        self.sale.is_applied = False
        with self.assertRaises(ValidationError):
            self.sale.save()
        self.item.quantity = Decimal("3")
        with self.assertRaises(ValidationError):
            self.item.save()
