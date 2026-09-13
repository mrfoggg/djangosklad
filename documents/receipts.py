"""Quantities available for receipt, excluding the document being edited."""
from decimal import Decimal

from django.db.models import DecimalField, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from .models import GoodsReceiptItem, OrderItem


def receipt_order_items(receipt, order_ids=None):
    if order_ids is None:
        order_ids = list(receipt.orders.values_list("pk", flat=True)) if receipt.pk else []
    received = GoodsReceiptItem.objects.filter(
        order_item_id=OuterRef("pk"), receipt__is_applied=True,
    )
    if receipt.pk:
        received = received.exclude(receipt_id=receipt.pk)
    received = received.order_by().values("order_item_id").annotate(total=Sum("quantity"))
    return OrderItem.objects.filter(
        purchase_order_id__in=order_ids,
        purchase_order__supplier_id__in=receipt.get_order_supplier_ids(),
        purchase_order__is_applied=True,
        purchase_order__to_remove=False,
    ).filter(
        Q(organization_id=receipt.organization_id)
        | Q(organization__isnull=True, purchase_order__organization_id=receipt.organization_id)
    ).select_related("product__unit", "purchase_order").order_by("purchase_order_id", "sort_order_purchase", "pk").annotate(
        received_quantity=Coalesce(
            Subquery(received.values("total")[:1]), Value(Decimal("0")),
            output_field=DecimalField(max_digits=14, decimal_places=6),
        ),
    ).annotate(remaining_quantity=F("quantity") - F("received_quantity"))
