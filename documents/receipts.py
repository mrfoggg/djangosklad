"""Quantities available for receipt, excluding the document being edited."""
from decimal import Decimal

from django.db.models import DecimalField, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from .models import GoodsReceiptItem, OrderItem


def receipt_order_items(receipt):
    received = GoodsReceiptItem.objects.filter(
        order_item_id=OuterRef("pk"), receipt__is_applied=True,
    )
    if receipt.pk:
        received = received.exclude(receipt_id=receipt.pk)
    received = received.order_by().values("order_item_id").annotate(total=Sum("quantity"))
    return OrderItem.objects.filter(
        purchase_order_id=receipt.purchase_order_id,
    ).filter(
        Q(organization_id=receipt.organization_id)
        | Q(organization__isnull=True, purchase_order__organization_id=receipt.organization_id)
    ).select_related("product__unit", "purchase_order").annotate(
        received_quantity=Coalesce(
            Subquery(received.values("total")[:1]), Value(Decimal("0")),
            output_field=DecimalField(max_digits=14, decimal_places=6),
        ),
    ).annotate(remaining_quantity=F("quantity") - F("received_quantity"))
