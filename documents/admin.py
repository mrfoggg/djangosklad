from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from djmoney.models.fields import MoneyField
from unfold.admin import ModelAdmin, TabularInline
from unfold.widgets import UnfoldAdminMoneyWidget

from .models import SupplierPriceItem, SupplierPriceList


class SupplierPriceItemInline(TabularInline):
    model = SupplierPriceItem
    extra = 1
    # Здесь можно будет добавить отображение цены в UAH,
    # если у поставщика включен признак use_usd_prices.
    fields = ("product", "price")
    formfield_overrides = {
        MoneyField: {"widget": UnfoldAdminMoneyWidget},
    }


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
