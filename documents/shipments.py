"""Available quantities for partial shipments of a sales document."""
from decimal import Decimal
from django.db.models import DecimalField, F, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from .models import SalesDocumentItem, ShipmentItem


def shipment_sales_items(shipment):
    shipped = ShipmentItem.objects.filter(sales_item_id=OuterRef("pk"), shipment__is_applied=True)
    current = shipped
    if shipment.pk:
        current = current.exclude(shipment_id=shipment.pk)
    def total(query):
        return Coalesce(
            Subquery(query.order_by().values("sales_item_id").annotate(total=Sum("quantity")).values("total")[:1]),
            Value(Decimal("0")), output_field=DecimalField(max_digits=14, decimal_places=6),
        )
    return SalesDocumentItem.objects.filter(
        document_id=shipment.sales_document_id, document__organization_id=shipment.organization_id,
        document__is_applied=True, document__to_remove=False,
    ).select_related("order_item__product__unit").annotate(
        remaining_quantity=F("quantity") - total(current),
        displayed_remaining=F("quantity") - total(shipped),
    )
