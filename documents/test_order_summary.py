from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import translation

from catalogs.models import Contractor, MeasurementUnit, Organization, Product, RetailStore, Warehouse
from documents.models import CustomerOrder, GoodsReceipt, GoodsReceiptItem, InvoiceItem, OrderItem, PaymentOrderOut, PaymentOutItem, PurchaseInvoice, PurchaseOrder
from documents.order_summary import supplier_order_summary


class SupplierOrderSummaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="Итоги")
        cls.supplier = Contractor.objects.create(last_name="Поставщик итогов", is_supplier=True)
        cls.warehouse = Warehouse.objects.create(name="Склад итогов", is_virtual=True)
        cls.product = Product.objects.create(name="Товар итогов", sku="order-summary")
        cls.order = PurchaseOrder.objects.create(supplier=cls.supplier, organization=cls.organization, is_applied=True)
        cls.line = OrderItem.objects.create(
            purchase_order=cls.order, organization=cls.organization, product=cls.product,
            warehouse=cls.warehouse, quantity=10, purchase_price=100,
        )

    def setUp(self):
        self.enterContext(translation.override("ru"))

    def receipt(self, quantity, applied=True, line=None):
        line = line or self.line
        receipt = GoodsReceipt.objects.create(supplier=self.supplier, organization=self.organization,
                                             warehouse=self.warehouse, is_applied=applied)
        receipt.orders.add(line.purchase_order_id)
        GoodsReceiptItem.objects.create(receipt=receipt, order_item=line, quantity=quantity)
        return receipt

    def invoice(self, quantity=10, applied=True):
        invoice = PurchaseInvoice.objects.create(supplier=self.supplier, organization=self.organization, is_applied=applied)
        invoice.orders.add(self.order)
        InvoiceItem.objects.create(invoice=invoice, order_item=self.line, quantity=quantity)
        return invoice

    def pay(self, invoice, amount, applied=True):
        payment = PaymentOrderOut.objects.create(organization=self.organization, contractor=self.supplier,
                                                amount=amount, bank_commission=10, is_applied=applied)
        PaymentOutItem.objects.create(payment=payment, invoice=invoice, amount=amount)
        return payment

    def test_partial_delivery_and_pending_amount_exclude_drafts(self):
        self.receipt(6)
        self.receipt(4, applied=False)
        summary = supplier_order_summary(self.order)
        self.assertEqual(summary["amounts"], {"ordered": Decimal("1000"), "received": Decimal("600"), "pending": Decimal("400")})
        self.assertEqual(summary["quantities"][0]["received"], Decimal("6"))
        self.assertEqual(summary["quantities"][0]["pending"], Decimal("4"))
        self.assertEqual(len(summary["receipts"]), 2)

    def test_mixed_units_are_not_added_together(self):
        unit = MeasurementUnit.objects.create(code="summary-kg", name="Килограмм", symbol="кг", decimal_places=3)
        product = Product.objects.create(name="Весовой", sku="summary-weight", unit=unit)
        OrderItem.objects.create(purchase_order=self.order, organization=self.organization, product=product,
                                 warehouse=self.warehouse, quantity=Decimal("1.250"), purchase_price=20)
        summary = supplier_order_summary(self.order)
        self.assertEqual(len(summary["quantities"]), 2)
        self.assertEqual(summary["amounts"]["ordered"], Decimal("1025"))

    def test_only_posted_allocations_count_and_commission_is_excluded(self):
        invoice = self.invoice(6)
        self.pay(invoice, 400)
        self.pay(invoice, 200, applied=False)
        summary = supplier_order_summary(self.order)
        self.assertEqual(summary["paid"], Decimal("400"))
        self.assertEqual(summary["balance"], Decimal("600"))
        self.assertFalse(summary["estimated"])

    def test_shared_invoice_and_receipt_only_include_this_orders_share(self):
        other = PurchaseOrder.objects.create(supplier=self.supplier, organization=self.organization, is_applied=True)
        other_line = OrderItem.objects.create(purchase_order=other, organization=self.organization,
                                             product=self.product, warehouse=self.warehouse, quantity=10, purchase_price=300)
        invoice = self.invoice()
        invoice.orders.add(other)
        InvoiceItem.objects.create(invoice=invoice, order_item=other_line, quantity=10)
        self.pay(invoice, 2000)
        receipt = self.receipt(4)
        receipt.orders.add(other)
        GoodsReceiptItem.objects.create(receipt=receipt, order_item=other_line, quantity=10)
        summary = supplier_order_summary(self.order)
        self.assertEqual(summary["paid"], Decimal("500"))
        self.assertEqual(summary["balance"], Decimal("500"))
        self.assertTrue(summary["estimated"])
        self.assertEqual(summary["amounts"]["received"], Decimal("400"))
        self.assertEqual(len(summary["invoices"]), 1)
        self.assertEqual(len(summary["receipts"]), 1)

    def test_overpayment_and_full_payment(self):
        invoice = self.invoice()
        self.pay(invoice, 1000)
        self.assertEqual(supplier_order_summary(self.order)["balance"], Decimal("0"))
        self.pay(invoice, 200)
        self.assertEqual(supplier_order_summary(self.order)["balance"], Decimal("-200"))
        html = str(admin.site._registry[PurchaseOrder].payment_summary(self.order))
        self.assertIn("Переплата по заказу", html)

    def test_unknown_prices_do_not_show_a_false_paid_status(self):
        self.line.purchase_price = None
        self.line.save()
        summary = supplier_order_summary(self.order)
        self.assertIsNone(summary["amounts"]["ordered"])
        self.assertIsNone(summary["balance"])

    def test_links_include_actual_line_connections_without_basis_selection(self):
        invoice = self.invoice()
        receipt = self.receipt(4)
        invoice.orders.clear()
        receipt.orders.clear()
        order_admin = admin.site._registry[PurchaseOrder]
        self.assertIn(reverse("admin:documents_purchaseinvoice_change", args=[invoice.pk]), str(order_admin.linked_invoices(self.order)))
        self.assertIn(reverse("admin:documents_goodsreceipt_change", args=[receipt.pk]), str(order_admin.linked_receipts(self.order)))

    def test_summary_fields_render_on_order_page(self):
        user = get_user_model().objects.create_superuser("summary-admin", password="test")
        self.client.force_login(user)
        self.invoice()
        self.receipt(4)
        response = self.client.get(reverse("admin:documents_purchaseorder_change", args=[self.order.pk]))
        self.assertEqual(response.status_code, 200)
        for label in ("Связанные счета", "Связанные поступления", "Итоги поставки", "Итоги оплаты заказа", "Ожидается"):
            self.assertContains(response, label)

    def test_invoice_link_shows_supplier_date_total_and_payment_difference(self):
        from datetime import date

        invoice = self.invoice()
        invoice.supplier_invoice_number = "СЧ-15"
        invoice.supplier_invoice_date = date(2026, 9, 10)
        invoice.save()
        self.pay(invoice, 400)
        order_admin = admin.site._registry[PurchaseOrder]
        html = str(order_admin.linked_invoices(PurchaseOrder.objects.get(pk=self.order.pk)))
        for value in (str(self.supplier), "10.09.2026", "СЧ-15", "Сумма документа", "Оплачено", "Не оплачено"):
            self.assertIn(value, html)
        self.assertIn(str(order_admin._money(Decimal("1000"))), html)
        self.assertIn(str(order_admin._money(Decimal("600"))), html)
        self.pay(invoice, 600)
        html = str(order_admin.linked_invoices(PurchaseOrder.objects.get(pk=self.order.pk)))
        self.assertIn("Оплачен", html)
        self.assertNotIn("Оплачено:", html)
        self.pay(invoice, 200)
        html = str(order_admin.linked_invoices(PurchaseOrder.objects.get(pk=self.order.pk)))
        self.assertIn("Переплата", html)
        self.assertIn(str(order_admin._money(Decimal("1200"))), html)
        self.assertIn(str(order_admin._money(Decimal("200"))), html)

    def test_receipt_link_shows_full_document_total_for_shared_receipt(self):
        from datetime import date

        receipt = self.receipt(4)
        receipt.supplier_delivery_note_date = date(2026, 9, 11)
        receipt.save()
        other = PurchaseOrder.objects.create(supplier=self.supplier, organization=self.organization, is_applied=True)
        line = OrderItem.objects.create(purchase_order=other, organization=self.organization, product=self.product,
                                        warehouse=self.warehouse, quantity=2, purchase_price=300)
        GoodsReceiptItem.objects.create(receipt=receipt, order_item=line, quantity=2)
        order_admin = admin.site._registry[PurchaseOrder]
        html = str(order_admin.linked_receipts(self.order))
        self.assertIn("11.09.2026", html)
        self.assertIn(str(self.supplier), html)
        self.assertIn(str(order_admin._money(Decimal("1000"))), html)
        self.assertNotIn("Оплачено", html)

    def test_document_links_show_fallback_date_and_ignore_draft_payments(self):
        invoice = self.invoice(applied=False)
        self.pay(invoice, 1000, applied=False)
        order_admin = admin.site._registry[PurchaseOrder]
        html = str(order_admin.linked_invoices(self.order))
        self.assertIn("Создан:", html)
        self.assertIn("Не оплачено:", html)
        self.assertIn(str(order_admin._money(Decimal("1000"))), html)


    def test_related_customer_orders_are_unique_and_show_full_sales_total(self):
        customer = Contractor.objects.create(last_name="Покупатель итогов", is_customer=True)
        store = RetailStore.objects.create(name="Магазин итогов")
        customer_order = CustomerOrder.objects.create(
            customer=customer, retail_store=store, organization=self.organization,
        )
        unrelated = CustomerOrder.objects.create(
            customer=customer, retail_store=store, organization=self.organization,
        )
        self.line.customer_order = customer_order
        self.line.customer_price = 150
        self.line.save()
        OrderItem.objects.create(
            purchase_order=self.order, customer_order=customer_order,
            organization=self.organization, product=self.product,
            warehouse=self.warehouse, quantity=2, purchase_price=100, customer_price=200,
        )
        OrderItem.objects.create(
            customer_order=customer_order, organization=self.organization, product=self.product,
            warehouse=self.warehouse, quantity=1, customer_price=300,
        )
        model_admin = admin.site._registry[PurchaseOrder]
        html = str(model_admin.linked_customer_orders(self.order))
        url = reverse("admin:documents_customerorder_change", args=[customer_order.pk])
        self.assertEqual(html.count(f'href="{url}"'), 1)
        self.assertNotIn(reverse("admin:documents_customerorder_change", args=[unrelated.pk]), html)
        self.assertIn("Покупатель итогов", html)
        self.assertIn("2200,00 грн", html)
        self.assertIn("Черновик", html)
        self.assertIn("linked_customer_orders", model_admin.readonly_fields)
        self.assertIn("linked_customer_orders", model_admin.fields)

    def test_related_customer_orders_empty_and_missing_prices(self):
        model_admin = admin.site._registry[PurchaseOrder]
        self.assertEqual(model_admin.linked_customer_orders(None), "—")
        self.assertEqual(model_admin.linked_customer_orders(PurchaseOrder()), "—")
        self.assertEqual(model_admin.linked_customer_orders(self.order), "—")
        customer = Contractor.objects.create(last_name="Без цены", is_customer=True)
        order = CustomerOrder.objects.create(
            customer=customer, retail_store=RetailStore.objects.create(name="Без цены"),
            organization=self.organization,
        )
        self.line.customer_order = order
        self.line.save()
        self.assertIn("Не определено: не заполнены цены", model_admin.linked_customer_orders(self.order))
