from decimal import Decimal

from django.test import TestCase
from catalogs.models import Contractor, Organization, Product, RetailStore, Warehouse
from documents.models import (
    PurchaseOrder, CustomerOrder, OrderItem, PurchaseInvoice, SalesInvoice,
    InvoiceItem, SalesInvoiceItem, GoodsReceipt, GoodsReceiptItem, SalesDocument, SalesDocumentItem,
)
from documents.admin import (
    PurchaseInvoiceItemInlineForm, SalesInvoiceItemInlineForm,
    GoodsReceiptItemForm, SalesDocumentItemForm,
)


class DisplayedRemainderTests(TestCase):
    def test_all_document_types_include_current_posting_in_label_only(self):
        org = Organization.objects.create(name="Остатки")
        party = Contractor.objects.create(last_name="Контрагент", is_supplier=True, is_customer=True)
        warehouse = Warehouse.objects.create(name="Остатки", is_virtual=True)
        product = Product.objects.create(name="Товар", sku="display-remainder")
        purchase = PurchaseOrder.objects.create(organization=org, supplier=party, is_applied=True)
        customer = CustomerOrder.objects.create(
            organization=org, customer=party, is_applied=True,
            retail_store=RetailStore.objects.create(name="Остатки"),
        )
        item = OrderItem.objects.create(
            purchase_order=purchase, customer_order=customer, organization=org,
            product=product, warehouse=warehouse, quantity=10, purchase_price=100, customer_price=150,
        )
        cases = [
            (PurchaseInvoice, InvoiceItem, PurchaseInvoiceItemInlineForm, "invoice", "invoice_context", purchase, {"supplier": party}, "включить в счета"),
            (SalesInvoice, SalesInvoiceItem, SalesInvoiceItemInlineForm, "invoice", "invoice_context", customer, {"customer": party}, "включить в счета"),
            (GoodsReceipt, GoodsReceiptItem, GoodsReceiptItemForm, "receipt", "receipt_context", purchase, {"supplier": party, "warehouse": warehouse}, "получить"),
            (SalesDocument, SalesDocumentItem, SalesDocumentItemForm, "document", "document_context", customer, {"customer": party}, "отгрузить"),
        ]
        for model, row_model, form_class, parent, context_key, order, values, label in cases:
            with self.subTest(model=model.__name__):
                document = model.objects.create(organization=org, is_applied=True, **values)
                document.orders.add(order)
                row = row_model.objects.create(**{parent: document}, order_item=item, quantity=Decimal("4"))
                def choice():
                    form = form_class(instance=row, **{context_key: document}, order_ids=[order.pk])
                    return form, form.fields["order_item"].queryset.get(pk=item.pk)
                form, selected = choice()
                self.assertEqual(selected.displayed_remaining, Decimal("6"))
                self.assertIn("| 10 ", form.fields["order_item"].label_from_instance(selected))
                allowance = getattr(selected, "invoice_remaining", getattr(selected, "remaining_quantity", None))
                self.assertEqual(allowance, Decimal("10"))
                self.assertIn(f"{label}: 6 ", form.fields["order_item"].label_from_instance(selected))
                row.quantity = Decimal("10")
                row.save()
                form, selected = choice()
                self.assertEqual(selected.displayed_remaining, Decimal("0"))
                self.assertIn(f"{label}: 0 ", form.fields["order_item"].label_from_instance(selected))
                document.is_applied = False
                document.save()
                form, selected = choice()
                self.assertEqual(selected.displayed_remaining, Decimal("10"))
