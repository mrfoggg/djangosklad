"""Quantity reserved by posted supplier invoices."""
from decimal import Decimal

from django.db.models import DecimalField, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from .models import InvoiceItem, OrderItem


def with_invoice_balance(queryset, invoice_id=None):
    billed = InvoiceItem.objects.filter(order_item_id=OuterRef("pk"), invoice__is_applied=True)
    if invoice_id:
        billed = billed.exclude(invoice_id=invoice_id)
    billed = billed.order_by().values("order_item_id").annotate(total=Sum("quantity"))
    return queryset.annotate(
        invoiced_quantity=Coalesce(
            Subquery(billed.values("total")[:1]), Value(Decimal("0")),
            output_field=DecimalField(max_digits=14, decimal_places=6),
        )
    ).annotate(invoice_remaining=F("quantity") - F("invoiced_quantity"))


def invoice_order_items(invoice, order_ids):
    suppliers = [invoice.supplier_id]
    if invoice.supplier_id and invoice.supplier.parent_holding_id:
        suppliers.append(invoice.supplier.parent_holding_id)
    return with_invoice_balance(OrderItem.objects.filter(
        purchase_order_id__in=order_ids,
        purchase_order__supplier_id__in=suppliers,
        purchase_order__is_applied=True,
        purchase_order__to_remove=False,
    ).filter(
        Q(organization_id=invoice.organization_id)
        | Q(organization__isnull=True, purchase_order__organization_id=invoice.organization_id)
    ).select_related("product__unit", "purchase_order"), invoice.pk)
