"""Display positions in the full supplier order, independent of choice filtering."""
from django.db.models import Count, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce

from .models import OrderItem


def with_order_position(queryset, customer=False):
    order_field = "customer_order_id" if customer else "purchase_order_id"
    sort_field = "sort_order_customer" if customer else "sort_order_purchase"
    previous = OrderItem.objects.annotate(
        position_sort=Coalesce(sort_field, Value(-1)),
    ).filter(**{order_field: OuterRef(order_field)}).filter(
        Q(position_sort__lt=OuterRef("position_sort"))
        | Q(position_sort=OuterRef("position_sort"), pk__lte=OuterRef("pk"))
    ).order_by().values(order_field).annotate(number=Count("pk"))
    return queryset.annotate(
        position_sort=Coalesce(sort_field, Value(-1)),
        order_position_number=Subquery(previous.values("number")[:1]),
    )
