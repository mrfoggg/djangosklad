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
    print(f"Supplier: {supplier_id}, Product: {product_id}")

    if not supplier_id or not product_id:
        return JsonResponse({"error": "Missing parameters"}, status=400)

    # Ищем в проведенных прайсах, исключая помеченные на удаление
    item = (
        SupplierPriceItem.objects.filter(
            document__supplier_id=supplier_id,
            document__is_applied=True,
            document__to_remove=False,
            product_id=product_id,
        )
        .select_related("document")
        .order_by("-document__dt_applied", "-id")
        .first()
    )

    print("item found:", item)

    if item and item.price:
        # Формируем дату: если dt_applied вдруг None, берем дату создания
        doc_date = item.document.dt_applied or item.document.dt_created

        return JsonResponse(
            {
                "price": str(item.price.amount),
                "currency": item.price.currency.code,
                "doc_number": item.document.id,
                "doc_date": doc_date.strftime("%d.%m.%Y"),
            }
        )

    return JsonResponse({"price": None})
