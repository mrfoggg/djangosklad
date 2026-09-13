from decimal import Decimal

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from catalogs.models import Contractor, Organization
from documents.models import CustomerOrder, OrderItem, SalesDocument, SalesDocumentItem, SalesInvoice, PaymentOrderIn, PaymentInItem
from documents.order_summary import customer_order_summary
from documents import test_customer_payment_links


class CustomerChainTests(TestCase):
    setUp = test_customer_payment_links.CustomerPaymentLinksTests.setUp
    post_invoice = test_customer_payment_links.CustomerPaymentLinksTests.post_invoice

    def action_url(self, action):
        return reverse(f"admin:documents_customerorder_{action}", args=[self.order.pk])

    def test_create_on_basis_opens_unsaved_forms_with_order_rows(self):
        for action in ("create_customer_invoice", "create_sales_document"):
            response = self.client.get(self.action_url(action), HTTP_HX_REQUEST="true")
            self.assertIn("HX-Redirect", response.headers)
            page = self.client.get(response.headers["HX-Redirect"])
            self.assertEqual(page.status_code, 200)
            form = page.context["adminform"].form
            self.assertEqual(form.initial["customer"], self.customer.pk)
            self.assertEqual(form.initial["orders"], [self.order.pk])
            rows = page.context["inline_admin_formsets"][0].formset.forms
            self.assertEqual(rows[0].initial["order_item"], self.item.pk)
            self.assertEqual(rows[0].initial["quantity"], Decimal("10"))
            self.assertContains(page, "Строка №1")
        self.assertFalse(SalesInvoice.objects.exists())
        self.assertFalse(SalesDocument.objects.exists())

    def test_multiple_organizations_dialog_filters_rows(self):
        other = Organization.objects.create(name="Вторая организация")
        other_item = OrderItem.objects.create(
            customer_order=self.order, organization=other, product=self.item.product,
            warehouse=self.item.warehouse, quantity=2, customer_price=200,
        )
        for action in ("create_customer_invoice", "create_sales_document"):
            response = self.client.get(self.action_url(action), HTTP_HX_REQUEST="true")
            self.assertContains(response, "Выберите организацию")
            self.assertEqual(set(response.context["form"].fields["organization"].queryset), {self.org, other})
            response = self.client.post(self.action_url(action), {
                "_form_submitted": "on", "organization": other.pk,
            }, HTTP_HX_REQUEST="true")
            page = self.client.get(response.headers["HX-Redirect"])
            rows = page.context["inline_admin_formsets"][0].formset.forms
            self.assertEqual([row.initial["order_item"] for row in rows], [other_item.pk])

    def test_source_preview_can_be_saved_without_editing_rows(self):
        response = self.client.get(self.action_url("create_sales_document"))
        result = self.client.post(response.url, {
            "customer": self.customer.pk, "organization": self.org.pk, "orders": [self.order.pk],
            "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "0",
            "items-0-order_item": self.item.pk, "items-0-quantity": "10", "items-0-sort_order": "0",
            "_save": "Save",
        })
        self.assertEqual(result.status_code, 302)
        self.assertEqual(SalesDocument.objects.get().items.get().order_item_id, self.item.pk)

    def sale(self, quantity=4):
        sale = SalesDocument.objects.create(customer=self.customer, organization=self.org, is_applied=True)
        sale.orders.add(self.order)
        SalesDocumentItem.objects.create(document=sale, order_item=self.item, quantity=quantity)
        return sale

    def test_posted_sale_locks_order_header_and_operational_reposting(self):
        self.sale()
        self.order.is_applied = False
        with self.assertRaises(ValidationError):
            self.order.save()
        self.order.refresh_from_db()
        self.order._force_current_date = True
        with self.assertRaises(ValidationError):
            self.order.clean()
        self.order._force_current_date = False
        self.order.comment = "Комментарий разрешён"
        self.order.save()
        from documents.admin import CustomerOrderForm
        form = CustomerOrderForm(data={
            "organization": self.org.pk, "customer": self.customer.pk,
            "retail_store": self.order.retail_store_id, "status": "new",
            "is_applied": "on", "force_current_date": "on",
        }, instance=self.order)
        self.assertFalse(form.is_valid())

    def test_summary_reports_sales_payments_and_links(self):
        self.assertEqual(self.post_invoice("6").status_code, 302)
        invoice = SalesInvoice.objects.get()
        sale = self.sale()
        payment = PaymentOrderIn.objects.create(
            organization=self.org, contractor=self.customer, amount=400,
            payment_number="1", is_applied=True,
        )
        PaymentInItem.objects.create(payment=payment, invoice=invoice, amount=400)
        summary = customer_order_summary(self.order)
        self.assertEqual(summary["amounts"], {"ordered": Decimal("1000"), "received": Decimal("400"), "pending": Decimal("600")})
        self.assertEqual(summary["paid"], Decimal("400"))
        self.assertEqual(summary["balance"], Decimal("600"))
        page = self.client.get(reverse("admin:documents_customerorder_change", args=[self.order.pk]))
        self.assertContains(page, reverse("admin:documents_salesinvoice_change", args=[invoice.pk]))
        self.assertContains(page, reverse("admin:documents_salesdocument_change", args=[sale.pk]))
        self.assertContains(page, "Итоги реализации")
        self.assertContains(page, "Не оплачено: 200,00 грн")

    def test_child_customer_can_sell_against_holding_order(self):
        from documents.sales import sales_order_items
        self.customer.legal_type = "HLD"
        self.customer.save()
        child = Contractor.objects.create(last_name="Дочерний покупатель", is_customer=True, parent_holding=self.customer)
        sale = SalesDocument(customer=child, organization=self.org)
        sale._selected_order_ids = [self.order.pk]
        sale.clean()
        self.assertEqual(sales_order_items(sale, [self.order.pk]).get().pk, self.item.pk)
        SalesDocumentItem(document=sale, order_item=self.item, quantity=Decimal("1")).clean()

    def test_draft_order_actions_forbidden(self):
        self.order.is_applied = False
        self.order.save()
        for action in ("create_customer_invoice", "create_sales_document"):
            self.assertEqual(self.client.get(self.action_url(action)).status_code, 403)
