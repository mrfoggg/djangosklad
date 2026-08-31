from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET

from catalogs.models import Contractor, Product

from .models import PurchaseOrder, RetailPriceItem, SupplierPriceItem


@staff_member_required
@require_GET
def get_latest_price_ajax(request):
    supplier_id = request.GET.get("supplier_id")
    product_id = request.GET.get("product_id")
    organization_id = request.GET.get("organization_id")
    requested_price_type = request.GET.get("price_type")
    purchase_order_id = request.GET.get("purchase_order_id")
    use_main_supplier = request.GET.get("use_main_supplier") == "1"

    if purchase_order_id:
        purchase_order = PurchaseOrder.objects.filter(pk=purchase_order_id).first()
        if not purchase_order:
            return JsonResponse({"error": "Purchase order not found"}, status=404)

        supplier_id = purchase_order.supplier_id
        organization_id = purchase_order.organization_id
        requested_price_type = purchase_order.price_type

    if use_main_supplier:
        product = (
            Product.objects.filter(pk=product_id)
            .select_related("main_supplier__supplier")
            .first()
        )
        if not product:
            return JsonResponse({"error": "Product not found"}, status=404)
        if not product.main_supplier_id:
            return JsonResponse(
                {
                    "price": "0",
                    "rrp": "0",
                    "currency": "UAH",
                    "status": "info",
                    "title": _("Основной поставщик не задан"),
                    "message": _("У товара не выбран основной поставщик."),
                    "supplier": "",
                }
            )
        supplier_id = product.main_supplier.supplier_id

    if not supplier_id or not product_id:
        return JsonResponse({"error": "Missing parameters"}, status=400)

    valid_price_types = set(Contractor.PriceType.values)
    supplier_default_price_type = Contractor.objects.filter(
        pk=supplier_id
    ).values_list("default_price_type", flat=True).first()
    price_type = (
        requested_price_type
        if requested_price_type in valid_price_types
        else supplier_default_price_type or Contractor.PriceType.WHOLESALE
    )
    price_field_by_type = {
        Contractor.PriceType.SMALL_WHOLESALE: "small_wholesale_price",
        Contractor.PriceType.WHOLESALE: "wholesale_price",
        Contractor.PriceType.LARGE_WHOLESALE: "large_wholesale_price",
    }
    price_field = price_field_by_type[price_type]

    # Исправленный запрос: убрали 'price' из select_related
    price_items = (
        SupplierPriceItem.objects.filter(
            document__supplier_id=supplier_id,
            document__is_applied=True,
            document__to_remove=False,
            product_id=product_id,
        )
        .select_related("document", "document__supplier")
        .order_by("-document__dt_applied", "-id")
    )

    # У цены для конкретной организации приоритет над общей ценой.
    # Если для организации прайса нет, используем прайс без организации.
    if organization_id:
        item = price_items.filter(document__organization_id=organization_id).first()
        if not item:
            item = price_items.filter(document__organization__isnull=True).first()
    else:
        item = price_items.filter(document__organization__isnull=True).first()

    response_data = {
        "price": "0",
        "rrp": "0",
        "currency": "UAH",
        "status": "info",
        "title": _("Цена не найдена"),
        "message": _("Нет проведенных прайсов. Установлено значение 0."),
        "supplier": "",
    }

    money_price = getattr(item, price_field, None) if item else None
    if money_price is not None:
        source_price = money_price.amount
        source_currency = money_price.currency.code
        rrp_source_price = item.price.amount
        rrp_currency = item.price.currency.code
        supplier = item.document.supplier

        target_price = source_price
        target_rrp = rrp_source_price
        doc_date = (item.document.dt_applied or item.document.created).strftime(
            "%d.%m.%Y"
        )

        # Подготавливаем детали для сообщения
        details = ""

        if source_currency == "USD" or rrp_currency == "USD":
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

        if source_currency == "USD":
            target_price = source_price * rate
            # Добавляем инфо о конвертации в скобки
            details = _(" (%(src_p)s %(src_c)s по курсу %(r)s)") % {
                "src_p": round(source_price, 2),
                "src_c": source_currency,
                "r": rate,
            }

        if rrp_currency == "USD":
            target_rrp = rrp_source_price * rate

        # Формируем единый формат сообщения
        # <strong>Цена грн</strong> (детали если есть)<br>Прайс №...
        message = _(
            "<strong>%(target)s грн</strong>%(details)s<br>Прайс №%(num)s от %(date)s"
        ) % {
            "target": round(target_price, 2),
            "details": details,
            "num": item.document.id,
            "date": doc_date,
        }

        response_data.update(
            {
                "price": str(round(target_price, 2)),
                "rrp": str(round(target_rrp, 2)),
                "status": "success",
                "title": _("Цена найдена"),
                "message": message,
                "supplier": str(supplier),
            }
        )

    return JsonResponse(response_data)


@staff_member_required
@require_GET
def get_latest_retail_price_ajax(request):
    retail_store_id = request.GET.get("retail_store_id")
    product_id = request.GET.get("product_id")

    if not product_id:
        return JsonResponse({"error": "Missing product_id"}, status=400)

    price_items = (
        RetailPriceItem.objects.filter(
            document__is_applied=True,
            document__to_remove=False,
            product_id=product_id,
        )
        .select_related("document", "document__retail_store")
        .order_by("-document__dt_applied", "-id")
    )

    # Цена конкретного магазина имеет приоритет над общей ценой для всех магазинов.
    item = None
    if retail_store_id:
        item = price_items.filter(document__retail_store_id=retail_store_id).first()
    if not item:
        item = price_items.filter(document__retail_store__isnull=True).first()

    response_data = {
        "price": "0",
        "currency": "UAH",
        "status": "info",
        "title": _("Цена не найдена"),
        "message": _("Нет проведенных розничных прайсов. Установлено значение 0."),
    }

    if item and item.price:
        doc_date = (item.document.dt_applied or item.document.created).strftime(
            "%d.%m.%Y"
        )
        store_name = item.document.retail_store or _("Все магазины")
        response_data.update(
            {
                "price": str(round(item.price.amount, 2)),
                "currency": item.price.currency.code,
                "status": "success",
                "title": _("Цена найдена"),
                "message": _(
                    "<strong>%(price)s %(currency)s</strong><br>"
                    "Прайс №%(num)s от %(date)s — %(store)s"
                )
                % {
                    "price": round(item.price.amount, 2),
                    "currency": item.price.currency.code,
                    "num": item.document.id,
                    "date": doc_date,
                    "store": store_name,
                },
            }
        )

    return JsonResponse(response_data)
