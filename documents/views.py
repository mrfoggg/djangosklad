# documents/views.py
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .models import SupplierPriceItem


@staff_member_required
@require_GET
def get_latest_price_ajax(request):
    print("RUN get_latest_price_ajax")

    supplier_id = request.GET.get("supplier_id")
    product_id = request.GET.get("product_id")
    print(supplier_id, product_id)

    if not supplier_id or not product_id:
        return JsonResponse({"error": "Missing parameters"}, status=400)

    # Ищем только в примененных и НЕ помеченных на удаление прайсах
    item = (
        SupplierPriceItem.objects.filter(
            document__supplier_id=supplier_id,
            document__is_applied=True,  # Только проведенные
            document__to_remove=False,  # Исключаем "корзину"
            product_id=product_id,
        )
        .select_related("document")
        .order_by("-document__dt_applied", "-id")
        .first()
    )

    print("item: ", item)

    if item and item.price:
        return JsonResponse(
            {"price": str(item.price.amount), "currency": item.price.currency.code}
        )

    return JsonResponse({"price": None})
