from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from djmoney.models.fields import MoneyField
from unfold.admin import ModelAdmin, TabularInline
from unfold.widgets import UnfoldAdminMoneyWidget

from .models import (
    CustomerOrder,
    OrderItem,
    PurchaseOrder,
    SupplierPriceItem,
    SupplierPriceList,
)


class SupplierPriceItemInline(TabularInline):
    model = SupplierPriceItem
    extra = 1
    # Здесь можно будет добавить отображение цены в UAH,
    # если у поставщика включен признак use_usd_prices.
    fields = ("product", "price")
    formfield_overrides = {
        MoneyField: {"widget": UnfoldAdminMoneyWidget},
    }


class PurchaseOrderItemInline(TabularInline):
    model = OrderItem
    extra = 1
    fields = (
        "sort_order_purchase",
        "product",
        "price",
        "quantity",
        "total_price",
        "customer_order",
        "warehouse",
    )
    readonly_fields = ("total_price",)


class CustomeOrderItemInline(TabularInline):
    model = OrderItem
    extra = 1
    fields = (
        "sort_order_customer",
        "product",
        "price",
        "quantity",
        "total_price",
        "purchase_order",
        "warehouse",
    )
    readonly_fields = ("total_price",)


@admin.register(SupplierPriceList)
class SupplierPriceListAdmin(ModelAdmin):
    list_display = ("id", "supplier", "dt_created", "is_applied", "to_remove")
    list_filter = ("is_applied", "to_remove", "supplier")
    search_fields = ("id", "supplier__last_name")

    inlines = [SupplierPriceItemInline]

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "id",
                    "supplier",
                    "is_applied",
                    "comment",
                )
            },
        ),
        (
            _("Служебная информация"),
            {
                "fields": ("dt_created", "dt_updated", "dt_applied", "to_remove"),
                "classes": ("collapse",),
            },
        ),
    )

    readonly_fields = ("id", "dt_created", "dt_updated", "dt_applied")


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(ModelAdmin):
    list_display = ("id", "supplier", "dt_created", "is_applied")
    list_filter = ("is_applied", "supplier")
    readonly_fields = ("id", "dt_created", "dt_updated", "dt_applied")

    inlines = [PurchaseOrderItemInline]

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "id",
                    "supplier",
                    "is_applied",
                    "comment",
                )
            },
        ),
        (
            "Даты",
            {
                "fields": ("dt_created", "dt_updated", "dt_applied"),
                "classes": ("collapse",),
            },
        ),
    )

    class Media:
        # Путь к файлу относительно папки static
        js = [
            "https://cdn.jsdelivr.net/npm/sweetalert2@11",
            "documents/js/admin_price_fetch.js",
        ]


# class SalesOrderItemInline(TabularInline):
#     model = CustomerOrderItem
#     extra = 1
#     # Здесь можно будет потом добавить AJAX для подтягивания розничной цены
#     tab = True


@admin.register(CustomerOrder)
class CusromerOrderAdmin(ModelAdmin):
    list_display = ["id", "customer", "status", "dt_created", "is_applied"]
    list_filter = ["status", "is_applied"]
    search_fields = ["customer", "id"]
    inlines = [CustomeOrderItemInline]
