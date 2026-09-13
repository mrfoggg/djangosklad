from decimal import Decimal
from django.contrib.auth import get_user_model
from django.forms import inlineformset_factory
from django.test import TestCase
from django.urls import reverse
from django.utils import translation
from catalogs.models import Contractor, Organization, OurBankAccount, Product, RetailStore, Warehouse
from documents.models import CustomerOrder, OrderItem, SalesInvoice, SalesInvoiceItem, PaymentOrderIn, PaymentInItem
from documents.admin import PaymentInItemInlineForm, PaymentInItemInlineFormSet
from documents.sales_invoices import with_sales_invoice_balance


class CustomerPaymentLinksTests(TestCase):
    def setUp(self):
        self.enterContext(translation.override("ru"))
        self.client.force_login(get_user_model().objects.create_superuser("customer-pay", password="test"))
        self.org = Organization.objects.create(name="Продажи")
        OurBankAccount.objects.create(organization=self.org, iban="UA-test", is_default=True)
        self.customer = Contractor.objects.create(last_name="Покупатель", is_customer=True)
        self.order = CustomerOrder.objects.create(
            organization=self.org, customer=self.customer, is_applied=True,
            retail_store=RetailStore.objects.create(name="Магазин"),
        )
        self.item = OrderItem.objects.create(
            customer_order=self.order, organization=self.org,
            product=Product.objects.create(name="Товар", sku="customer-pay"),
            warehouse=Warehouse.objects.create(name="Склад", is_virtual=True),
            quantity=10, customer_price=100, purchase_price=50,
        )

    def post_invoice(self, quantity=None, fill=False):
        data = {
            "organization": self.org.pk, "customer": self.customer.pk, "orders": [self.order.pk],
            "is_applied": "on", "items-TOTAL_FORMS": "0" if fill else "1",
            "items-INITIAL_FORMS": "0", "_save": "Save",
        }
        if fill:
            data["fill_from_orders"] = "on"
        else:
            data.update({"items-0-order_item": self.item.pk, "items-0-quantity": quantity,
                         "items-0-sort_order": "0"})
        return self.client.post(reverse("admin:documents_salesinvoice_add"), data)

    def test_partial_invoices_and_fill_remaining(self):
        self.assertEqual(self.post_invoice("6").status_code, 302)
        self.assertEqual(SalesInvoice.objects.get().items.get().total_price, Decimal("600"))
        self.assertEqual(self.post_invoice(fill=True).status_code, 302)
        self.assertEqual(SalesInvoice.objects.latest("pk").items.get().quantity, Decimal("4"))
        self.assertEqual(self.item.sales_invoice_items.count(), 2)
        self.assertEqual(with_sales_invoice_balance(OrderItem.objects.all()).get().invoice_remaining, 0)

    def test_excess_invoice_quantity_rejected(self):
        response = self.post_invoice("11")
        self.assertEqual(response.status_code, 200)
        self.assertIn("quantity", response.context["inline_admin_formsets"][0].formset.errors[0])
        self.assertFalse(SalesInvoice.objects.exists())

    def allocations(self, invoice, amount, rows):
        payment = PaymentOrderIn(organization=self.org, contractor=self.customer,
                                 amount=Decimal(amount), payment_number="1")
        factory = inlineformset_factory(
            PaymentOrderIn, PaymentInItem, form=PaymentInItemInlineForm,
            formset=PaymentInItemInlineFormSet, fields=("invoice", "amount", "sort_order"), extra=0,
        )
        data = {"organization": self.org.pk, "contractor": self.customer.pk,
                "paymentinitem_set-TOTAL_FORMS": str(len(rows)), "paymentinitem_set-INITIAL_FORMS": "0"}
        for i, value in enumerate(rows):
            data.update({f"paymentinitem_set-{i}-invoice": invoice.pk,
                         f"paymentinitem_set-{i}-amount": value,
                         f"paymentinitem_set-{i}-sort_order": str(i)})
        return factory(data, instance=payment)

    def test_payment_autofill_uses_partial_invoice_and_posted_payments(self):
        self.assertEqual(self.post_invoice("6").status_code, 302)
        invoice = SalesInvoice.objects.get()
        posted = PaymentOrderIn.objects.create(organization=self.org, contractor=self.customer,
                                               amount=200, payment_number="2", is_applied=True)
        PaymentInItem.objects.create(payment=posted, invoice=invoice, amount=200)
        draft = PaymentOrderIn.objects.create(organization=self.org, contractor=self.customer,
                                              amount=100, payment_number="3")
        PaymentInItem.objects.create(payment=draft, invoice=invoice, amount=100)
        forms = self.allocations(invoice, "400", [""])
        self.assertTrue(forms.is_valid(), forms.errors)
        self.assertEqual(forms.forms[0].instance.amount, Decimal("400"))
        forms.instance.save()
        forms.save()
        self.assertEqual(forms.instance.paymentinitem_set.get().amount, Decimal("400"))

    def test_allocation_total_and_wrong_customer_are_rejected(self):
        self.assertEqual(self.post_invoice("6").status_code, 302)
        invoice = SalesInvoice.objects.get()
        forms = self.allocations(invoice, "500", ["300", "300"])
        self.assertFalse(forms.is_valid())
        self.assertTrue(forms.non_form_errors())
        invoice.customer = Contractor.objects.create(last_name="Другой")
        invoice.save()
        forms = self.allocations(invoice, "600", ["600"])
        self.assertFalse(forms.is_valid())
        self.assertIn("invoice", forms.errors[0])

    def test_incoming_payment_admin_saves_allocation(self):
        self.assertEqual(self.post_invoice("6").status_code, 302)
        invoice = SalesInvoice.objects.get()
        response = self.client.post(reverse("admin:documents_paymentorderin_add"), {
            "organization": self.org.pk, "contractor": self.customer.pk,
            "payment_number": "BANK-1", "category": "goods_services", "amount": "600",
            "paymentinitem_set-TOTAL_FORMS": "1", "paymentinitem_set-INITIAL_FORMS": "0",
            "paymentinitem_set-0-invoice": invoice.pk, "paymentinitem_set-0-amount": "",
            "paymentinitem_set-0-sort_order": "0", "_save": "Save",
        })
        self.assertEqual(response.status_code, 302)
        payment = PaymentOrderIn.objects.get()
        self.assertEqual(payment.paymentinitem_set.get().amount, Decimal("600"))
        self.assertEqual(list(payment.sales_invoices.all()), [invoice])
