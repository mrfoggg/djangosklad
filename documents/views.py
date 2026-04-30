from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET

from .models import SupplierPriceItem


@staff_member_required
@require_GET
def get_latest_price_ajax(request):
    supplier_id = request.GET.get("supplier_id")
    product_id = request.GET.get("product_id")

    if not supplier_id or not product_id:
        return JsonResponse({"error": "Missing parameters"}, status=400)

    # Исправленный запрос: убрали 'price' из select_related
    item = (
        SupplierPriceItem.objects.filter(
            document__supplier_id=supplier_id,
            document__is_applied=True,
            document__to_remove=False,
            product_id=product_id,
        )
        .select_related("document", "document__supplier")
        .order_by("-document__dt_applied", "-id")
        .first()
    )

    response_data = {
        "price": "0",
        "currency": "UAH",
        "status": "info",
        "title": _("Цена не найдена"),
        "message": _("Нет проведенных документов. Установлено значение 0."),
    }

    if item and item.price:
        source_price = item.price.amount
        source_currency = item.price.currency.code
        supplier = item.document.supplier

        target_price = source_price
        doc_date = (item.document.dt_applied or item.document.dt_created).strftime(
            "%d.%m.%Y"
        )

        # Логика конвертации
        if source_currency == "USD":
            rate = getattr(supplier, "usd_rate", None)

            if not rate or rate <= 0:
                return JsonResponse(
                    {
                        "price": "0",
                        "status": "error",
                        "title": _("Ошибка курса"),
                        "message": _("У поставщика %(name)s не задан курс!")
                        % {"name": supplier.last_name},
                    }
                )

            target_price = source_price * rate

            # Формируем сообщение с деталями конвертации
            message = _(
                "<strong>%(target)s грн</strong> (%(src_p)s %(src_c)s по курсу %(r)s)<br>"
                "Прайс №%(num)s от %(date)s"
            ) % {
                "target": round(target_price, 2),
                "src_p": round(source_price, 2),
                "src_c": source_currency,
                "r": rate,
                "num": item.document.id,
                "date": doc_date,
            }
        else:
            # Если уже в гривне
            message = _("Прайс №%(num)s от %(date)s") % {
                "num": item.document.id,
                "date": doc_date,
            }

        response_data.update(
            {
                "price": str(round(target_price, 2)),
                "status": "success",
                "title": _("Цена найдена"),
                "message": message,
            }
        )

    return JsonResponse(response_data)
