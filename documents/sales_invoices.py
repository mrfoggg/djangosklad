"""Quantity reserved by posted customer invoices."""
from decimal import Decimal

from django.db.models import DecimalField, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from .order_lines import with_order_position
from .models import SalesInvoiceItem, OrderItem


def with_sales_invoice_balance(queryset, invoice_id=None):
    billed = SalesInvoiceItem.objects.filter(order_item_id=OuterRef("pk"), invoice__is_applied=True)
    if invoice_id:
        billed = billed.exclude(invoice_id=invoice_id)
    billed = billed.order_by().values("order_item_id").annotate(total=Sum("quantity"))
    queryset = queryset.annotate(
        invoiced_quantity=Coalesce(
            Subquery(billed.values("total")[:1]), Value(Decimal("0")),
            output_field=DecimalField(max_digits=14, decimal_places=6),
        )
    ).annotate(invoice_remaining=F("quantity") - F("invoiced_quantity"))

    return with_order_position(queryset, customer=True)


def sales_invoice_order_items(invoice, order_ids):
    customers = [invoice.customer_id]
    if invoice.customer_id and invoice.customer.parent_holding_id:
        customers.append(invoice.customer.parent_holding_id)
    return with_sales_invoice_balance(OrderItem.objects.filter(
        customer_order_id__in=order_ids,
        customer_order__customer_id__in=customers,
        customer_order__is_applied=True,
        customer_order__to_remove=False,
    ).filter(
        Q(organization_id=invoice.organization_id)
        | Q(organization__isnull=True, customer_order__organization_id=invoice.organization_id)
    ).select_related("product__unit", "customer_order"), invoice.pk)
