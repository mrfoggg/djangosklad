from django import forms
from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from djmoney.models.fields import MoneyField
from unfold.admin import ModelAdmin, TabularInline
from unfold.widgets import UnfoldAdminMoneyWidget
from unfold.widgets import UnfoldAdminDecimalFieldWidget, UnfoldAdminSelectWidget, UnfoldAdminSplitDateTimeVerticalWidget
from django.forms.models import BaseInlineFormSet

from catalogs.models import BankAccount

from .models import (
    CustomerOrder,
    OrderItem,
    PurchaseInvoice,
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
                    # Блокируем все поля, КРОМЕ полей сортировки
                    if "sort_order" not in name:
                        field.disabled = True

    class Meta:
        widgets = {
	        'product': UnfoldAdminSelectWidget(attrs={
	            'style': 'width: 250px;', # Жесткая фиксация
	        }),
            'price': UnfoldAdminDecimalFieldWidget(attrs={
                'style': 'width: 120px;', # Жесткая фиксация
            }),
            'quantity': UnfoldAdminDecimalFieldWidget(attrs={
                'style': 'width: 90px;', # Жесткая фиксация
            }),
        }

class OrderItemInlineFormSet(BaseInlineFormSet):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Перебираем поля базовой формы, которая используется в наборе
        # Это изменит label для всех строк инлайна сразу
        if "sort_order_purchase" in self.form.base_fields:
            self.form.base_fields["sort_order_purchase"].label = "⇅"

        if "sort_order_customer" in self.form.base_fields:
            self.form.base_fields["sort_order_customer"].label = "⇅"

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        for form in self.forms:
            if self.can_delete and self._should_delete_form(form):
                instance = form.instance
                if instance and instance.pk:
                    # Определяем, какой именно документ блокирует удаление
                    applied_doc = None
                    if instance.purchase_order and instance.purchase_order.is_applied:
                        applied_doc = instance.purchase_order
                    elif instance.customer_order and instance.customer_order.is_applied:
                        applied_doc = instance.customer_order

                    if applied_doc:
                        # Используем f-строку для вывода названия документа
                        raise forms.ValidationError(
                            f"Нельзя удалить строку: документ '{applied_doc}' уже проведен."
                        )

# для заказа поставщику
class PurchaseOrderItemInline(TabularInline):
    model = OrderItem
    form = OrderItemInlineForm
    formset = OrderItemInlineFormSet
    extra = 0
    # tab = True
    fields = (
        "sort_order_purchase",
        "product",
        "price",
        "quantity",
        "total_price",
        "customer_order",
        "warehouse",
    )
    ordering = ("sort_order_purchase",)
    readonly_fields = ("total_price",)
    # def get_formset(self, request, obj=None, **kwargs):
    #     formset = super().get_formset(request, obj, **kwargs)
    #     # Подменяем label для конкретного поля в форме инлайна
    #     formset.form.base_fields["sort_order_purchase"].label = "Сортировка"
    #     return formset


# для заказа покупателю
class CustomeOrderItemInline(TabularInline):
    model = OrderItem
    form = OrderItemInlineForm
    formset = OrderItemInlineFormSet
    extra = 0

    fields = (
        "sort_order_customer",
        "product",
        "price",
        "quantity",
        "total_price",
        "purchase_order",
        "warehouse",
    )
    ordering = ("sort_order_customer",)
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

# Заказ поставщику
@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(BaseDocumentAdmin):
    form = PurchaseOrderForm
    list_filter = ("is_applied", "supplier")
    inlines = [PurchaseOrderItemInline]

    class Media:
        js = [
            "https://cdn.jsdelivr.net/npm/sweetalert2@11",
            "https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js",
            "documents/js/admin_price_fetch.js",
            "documents/js/admin_sortable_init.js",
        ]

class CustomerOrderForm(DocumentForm):
    class Meta:
        model = CustomerOrder
        fields = "__all__"

# Заказ покупателя
@admin.register(CustomerOrder)
class CustomerOrderAdmin(BaseDocumentAdmin):
    form = CustomerOrderForm

    list_filter = ["status", "is_applied"]
    search_fields = ["customer", "id"]
    readonly_fields = BASE_READONLY
    fields = BASE_FIELDS + ("customer", "status")
    inlines = [CustomeOrderItemInline]

    class Media:
        js = [
            "https://cdn.jsdelivr.net/npm/sweetalert2@11",
            "https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js",
            "documents/js/admin_price_fetch.js",
            "documents/js/admin_sortable_init.js",
        ]

class PurchaseInvoiceForm(DocumentForm):
    class Meta:
        model = PurchaseInvoice
        fields = "__all__"


@admin.register(PurchaseInvoice)
class PurchaseInvoiceAdmin(BaseDocumentAdmin):
    form = PurchaseInvoiceForm
    list_filter = ("is_applied", "supplier")
    readonly_fields = BASE_READONLY
    fields = BASE_FIELDS + ("supplier", "bank_account")

    # inlines = [PurchaseOrderItemInline]
    #
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """
        Фильтруем список банковских счетов в зависимости от выбранного поставщика.
        """
        if db_field.name == "bank_account":
            # Пытаемся получить ID объекта из URL (режим редактирования)
            object_id = request.resolver_match.kwargs.get("object_id")

            if object_id:
                # Если мы редактируем существующий инвойс,
                # получаем его из базы, чтобы узнать поставщика
                invoice = self.get_object(request, object_id)
                if invoice and invoice.supplier:
                    kwargs["queryset"] = BankAccount.objects.filter(
                        contractor=invoice.supplier
                    )
                else:
                    kwargs["queryset"] = BankAccount.objects.none()
            else:
                # При создании нового документа поставщик еще не выбран в БД.
                # Список счетов будет пуст до первого сохранения.
                kwargs["queryset"] = BankAccount.objects.none()

        return super().formfield_for_foreignkey(db_field, request, **kwargs)
