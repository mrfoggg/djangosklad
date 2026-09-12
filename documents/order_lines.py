"""Display positions in the full supplier order, independent of choice filtering."""
from django.db.models import Count, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce

from .models import OrderItem


def with_order_position(queryset):
    previous = OrderItem.objects.annotate(
        position_sort=Coalesce("sort_order_purchase", Value(-1)),
    ).filter(purchase_order_id=OuterRef("purchase_order_id")).filter(
        Q(position_sort__lt=OuterRef("position_sort"))
        | Q(position_sort=OuterRef("position_sort"), pk__lte=OuterRef("pk"))
    ).order_by().values("purchase_order_id").annotate(number=Count("pk"))
    return queryset.annotate(
        position_sort=Coalesce("sort_order_purchase", Value(-1)),
        order_position_number=Subquery(previous.values("number")[:1]),
    )
