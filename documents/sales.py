"""Quantities available for document, excluding the document being edited."""
from decimal import Decimal

from django.db.models import DecimalField, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from .order_lines import with_order_position
from .models import SalesDocumentItem, OrderItem


def sales_order_items(document, order_ids=None):
    if order_ids is None:
        order_ids = list(document.orders.values_list("pk", flat=True)) if document.pk else []
    sold = SalesDocumentItem.objects.filter(
        order_item_id=OuterRef("pk"), document__is_applied=True,
    )
    if document.pk:
        sold = sold.exclude(document_id=document.pk)
    sold = sold.order_by().values("order_item_id").annotate(total=Sum("quantity"))
    queryset = OrderItem.objects.filter(
        customer_order_id__in=order_ids,
        customer_order__customer_id=document.customer_id,
        customer_order__is_applied=True,
        customer_order__to_remove=False,
    ).filter(
        Q(organization_id=document.organization_id)
        | Q(organization__isnull=True, customer_order__organization_id=document.organization_id)
    ).select_related("product__unit", "customer_order").order_by("customer_order_id", "sort_order_customer", "pk").annotate(
        sold_quantity=Coalesce(
            Subquery(sold.values("total")[:1]), Value(Decimal("0")),
            output_field=DecimalField(max_digits=14, decimal_places=6),
        ),
    ).annotate(remaining_quantity=F("quantity") - F("sold_quantity"))

    return with_order_position(queryset, customer=True)
