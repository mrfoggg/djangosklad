from django import forms
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

BASE_READONLY_DATES = ("created", "updated")
BASE_READONLY = ("id",) + BASE_READONLY_DATES
BASE_FIELDS = (
    # (BASE_READONLY),
    BASE_READONLY,
    "dt_applied",
    ("is_applied", "force_current_date"),
    "to_remove",
)
BASE_FIELDSETS = ((None, {"fields": BASE_FIELDS}),)


class DocumentForm(forms.ModelForm):
    force_current_date = forms.BooleanField(
        label=_("Провести оперативно"),
        required=False,
        initial=False,
        help_text=_("Установит текущую дату проведения"),
    )


class BaseDocumentAdmin(ModelAdmin):
    readonly_fields = BASE_READONLY
    conditional_fields = {
        "to_remove": "is_applied == false",
        "is_applied": "to_remove == false",
        "dt_applied": "to_remove == false",
        "force_current_date": "is_applied == true",
        "status": "to_remove == false",
    }

    def save_model(self, request, obj, form, change):
        # print("SAVE, force_current_date:", form.cleaned_data.get("force_current_date"))
        obj._force_current_date = form.cleaned_data.get("force_current_date", False)

        # obj.user = request.user
        super().save_model(request, obj, form, change)


# def save(self, request, obj, form, change):
#     print("SAVE")
#     obj._force_current_date = form.cleaned_data.get("force_current_date", False)
#     super().save_model(request, obj, form, change)


class SupplierPriceItemInline(TabularInline):
    model = SupplierPriceItem
    extra = 1
    fields = ("product", "price")
    formfield_overrides = {
        MoneyField: {"widget": UnfoldAdminMoneyWidget},
    }


class OrderItemInlineForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            purchase_applied = (
                self.instance.purchase_order and self.instance.purchase_order.is_applied
            )
            customer_applied = (
                self.instance.customer_order and self.instance.customer_order.is_applied
            )

            if purchase_applied or customer_applied:
                for name, field in self.fields.items():
                    field.disabled = True

    def clean(self):
        # Дополнительная защита: запрещаем удаление, если связаны проведенные доки
        if self.instance and self.instance.pk:
            if (
                self.instance.purchase_order and self.instance.purchase_order.is_applied
            ) or (
                self.instance.customer_order and self.instance.customer_order.is_applied
            ):
                if self.cleaned_data.get("DELETE"):
                    raise forms.ValidationError(
                        "Нельзя удалить строку, связанную с проведенным документом"
                    )
        return super().clean()


class PurchaseOrderItemInline(TabularInline):
    model = OrderItem
    form = OrderItemInlineForm
    ordering_field = "sort_order_purchase"
    hide_ordering_field = True
    extra = 0
    # tab = True
    fields = (
        # "sort_order_purchase",
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
    form = OrderItemInlineForm
    ordering_field = "sort_order_customer"
    hide_ordering_field = True
    extra = 0

    # КЛЮЧЕВОЙ МОМЕНТ:
    # Используем list_display для активации JS и вывода иконки
    list_display = ("sort_order_customer", "product", "price")

    # В fields указываем порядок, НО поле сортировки НЕ добавляем туда как обычную строку
    # Мы добавим его в readonly_fields или воспользуемся тем, что Unfold
    # сам должен его подтянуть, если оно указано в ordering_field.
    # fields = (
    #     "product",
    #     "price",
    #     "quantity",
    #     "total_price",
    #     "purchase_order",
    #     "warehouse",
    # )
    readonly_fields = ("total_price",)

    # Если после этого ошибка "input is null" осталась, добавь поле в readonly_fields:
    # readonly_fields = ("total_price", "sort_order_customer")


class SupplierPriceListForm(DocumentForm):
    class Meta:
        model = SupplierPriceList
        fields = "__all__"


@admin.register(SupplierPriceList)
class SupplierPriceListAdmin(BaseDocumentAdmin):
    form = SupplierPriceListForm
    list_filter = ("is_applied", "to_remove", "supplier")
    search_fields = ("id", "supplier__last_name")
    inlines = [SupplierPriceItemInline]
    fields = BASE_FIELDS + ("supplier",)
    readonly_fields = BASE_READONLY


class PurchaseOrderForm(DocumentForm):
    class Meta:
        model = PurchaseOrder
        fields = "__all__"


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(ModelAdmin):
    form = PurchaseOrderForm
    list_filter = ("is_applied", "supplier")
    readonly_fields = BASE_READONLY
    fields = BASE_FIELDS + ("supplier",)
    inlines = [PurchaseOrderItemInline]

    class Media:
        # Путь к файлу относительно папки static
        js = [
            "https://cdn.jsdelivr.net/npm/sweetalert2@11",
            "documents/js/admin_price_fetch.js",
        ]


class CustomerOrderForm(DocumentForm):
    class Meta:
        model = CustomerOrder
        fields = "__all__"


@admin.register(CustomerOrder)
class CustomerOrderAdmin(BaseDocumentAdmin):
    form = CustomerOrderForm

    list_filter = ["status", "is_applied"]
    search_fields = ["customer", "id"]
    readonly_fields = BASE_READONLY
    fields = BASE_FIELDS + ("customer", "status")
    inlines = [CustomeOrderItemInline]
