from django import forms
from django.contrib import admin
from django.db.models import Q
from django.forms.models import BaseInlineFormSet
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from djmoney.models.fields import MoneyField
from unfold.admin import ModelAdmin, TabularInline
from unfold.widgets import (
    UnfoldAdminDecimalFieldWidget,
    UnfoldAdminMoneyWidget,
    UnfoldAdminSelectWidget,
    UnfoldAdminSplitDateTimeVerticalWidget,
)

from catalogs.models import ContractorBankAccount

from .models import (
    CustomerOrder,
    InvoiceItem,
    OrderItem,
    PaymentOrderOut,
    PaymentOutItem,
    PurchaseInvoice,
    PurchaseOrder,
    RetailPriceItem,
    RetailPriceList,
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
    "organization",
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
    fields = (
        "product",
        "small_wholesale_price",
        "wholesale_price",
        "large_wholesale_price",
        "price",
    )
    formfield_overrides = {
        MoneyField: {"widget": UnfoldAdminMoneyWidget},
    }


class RetailPriceItemInline(TabularInline):
    model = RetailPriceItem
    extra = 1
    fields = ("product", "price", "supplier_price_info")
    readonly_fields = ("supplier_price_info",)
    formfield_overrides = {
        MoneyField: {"widget": UnfoldAdminMoneyWidget},
    }

    @admin.display(description=_("Прайс основного поставщика"))
    def supplier_price_info(self, obj):
        return format_html(
            '<div class="supplier-price-info text-sm whitespace-nowrap" '
            'data-purchase-price="" data-rrp="">{}</div>',
            _("Выберите товар"),
        )


class OrderItemInlineForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = getattr(self, "instance", None)
        if not instance or not instance.pk:
            return

        # Проверяем проведение заказов
        is_locked = (
            instance.purchase_order and instance.purchase_order.is_applied
        ) or (instance.customer_order and instance.customer_order.is_applied)

        if is_locked:
            allowed_fields = [
                "sort_order_purchase",
                "sort_order_customer",
                "payment_method_purchase",
                "payment_method_customer",
            ]

            # Проверяем, существует ли уже связанный счет
            has_invoice = hasattr(instance, "invoice_item") and instance.invoice_item

            for name, field in self.fields.items():
                # Блокируем поле, если его нет в разрешенных
                # ИЛИ если это метод оплаты при уже выставленном счете
                is_payment_method = name in ["payment_method_purchase", "payment_method_customer"]

                if (name not in allowed_fields) or (is_payment_method and has_invoice):
                    field.disabled = True

            # for name, field in self.fields.items():
            #     if name not in allowed_fields:
            #         field.disabled = True

            #     # Дополнительная блокировка для методов оплаты:
            #     # Если счет уже выставлен, менять метод оплаты нельзя (он зафиксирован как PREPAID)
            #     elif (
            #         name in ["payment_method_purchase", "payment_method_customer"]
            #         and has_invoice
            #     ):
            #         field.disabled = True

    class Meta:
        widgets = {
            "product": UnfoldAdminSelectWidget(
                attrs={
                    "style": "width: 250px;",  # Жесткая фиксация
                }
            ),
            "purchase_price": UnfoldAdminDecimalFieldWidget(
                attrs={
                    "style": "width: 120px;",
                }
            ),
            "customer_price": UnfoldAdminDecimalFieldWidget(
                attrs={
                    "style": "width: 120px;",  # Жесткая фиксация
                }
            ),
            "rrp": UnfoldAdminDecimalFieldWidget(
                attrs={
                    "style": "width: 120px;",
                }
            ),
            "quantity": UnfoldAdminDecimalFieldWidget(
                attrs={
                    "style": "width: 90px;",  # Жесткая фиксация
                }
            ),
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
        "purchase_price",
        "rrp",
        "quantity",
        "purchase_total_price",
        "organization",
        "customer_order",
        "warehouse",
        "payment_method_purchase",
        "get_invoice_link",
    )
    ordering = ("sort_order_purchase",)
    readonly_fields = ("purchase_total_price", "get_invoice_link")

    @admin.display(description=_("Счет"))
    def get_invoice_link(self, obj):
        # Проверяем, есть ли обратная связь от InvoiceItem
        if hasattr(obj, "invoice_item") and obj.invoice_item:
            invoice = obj.invoice_item.invoice
            url = reverse("admin:documents_purchaseinvoice_change", args=[invoice.id])

            return format_html(
                '<a href="{}" target="_blank" style="font-weight: 600; color: #10b981; text-decoration: underline;">Счет №{}</a>',
                url,
                invoice.id,
            )
        return "-"


# для заказа покупателю
class CustomeOrderItemInline(TabularInline):
    model = OrderItem
    form = OrderItemInlineForm
    formset = OrderItemInlineFormSet
    extra = 0

    fields = (
        "sort_order_customer",
        "product",
        "customer_price",
        "purchase_price",
        "rrp",
        "quantity",
        "customer_total_price",
        "purchase_order",
        "warehouse",
        "payment_method_customer",
    )
    ordering = ("sort_order_customer",)
    readonly_fields = ("customer_total_price",)


class PurchaseInvoiceItemInlineForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "order_item" in self.fields:
            # Переопределяем отображение каждой строки в выпадающем списке
            self.fields["order_item"].label_from_instance = self.label_for_purchase

    def label_for_purchase(self, obj):
        # Формируем строку: Заказ №X | Товар | Кол-во
        order_no = obj.purchase_order.id if obj.purchase_order else "???"
        order_dt = obj.purchase_order.dt_applied if obj.purchase_order else "???"
        return f"№{order_no} от {order_dt} | {obj.product.name} ({obj.quantity} шт.)"


class InvoiceItemInline(TabularInline):
    model = InvoiceItem
    form = PurchaseInvoiceItemInlineForm
    extra = 0
    tab = True

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        if "sort_order" in formset.form.base_fields:
            formset.form.base_fields["sort_order"].label = "⇅"
        return formset

    # Добавляем get_order_link в список полей
    fields = ("sort_order", "get_order_link", "order_item", "get_price", "get_total")
    readonly_fields = ("get_order_link", "get_price", "get_total")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "order_item":
            object_id = request.resolver_match.kwargs.get("object_id")
            if object_id:
                invoice = PurchaseInvoice.objects.filter(pk=object_id).first()
                if invoice:
                    # 1. Берем ID всех заказов, выбранных в "основаниях"
                    selected_order_ids = invoice.orders.values_list("id", flat=True)

                    # 2. Базовые фильтры айтемов
                    item_filters = Q(
                        purchase_order_id__in=selected_order_ids,
                        payment_method_purchase=OrderItem.PaymentMethod.PREPAID,
                    )

                    # 3. Условие "свободности" (нет счета или привязан к текущему)
                    availability_filter = Q(invoice_item__isnull=True) | Q(
                        invoice_item__invoice=invoice
                    )

                    # 4. Фильтр по организации (если в инвойсе она задана)
                    if invoice.organization:
                        item_filters &= Q(organization=invoice.organization)

                    kwargs["queryset"] = OrderItem.objects.filter(
                        item_filters, availability_filter
                    ).distinct()
                else:
                    kwargs["queryset"] = OrderItem.objects.none()
            else:
                kwargs["queryset"] = OrderItem.objects.none()

        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(description=_("Заказ"))
    def get_order_link(self, obj):
        # Проверяем наличие связи, чтобы не упасть с ошибкой
        if obj.order_item and obj.order_item.purchase_order:
            order = obj.order_item.purchase_order

            # Генерируем URL к странице редактирования заказа
            # documents — это имя твоего приложения (app_name)
            url = reverse("admin:documents_purchaseorder_change", args=[order.id])

            return format_html(
                '<a href="{}" target="_blank" style="font-weight: 600; color: #3b82f6; text-decoration: underline;">{}/{}</a>',
                url,
                order.id,
                order.dt_applied.strftime("%Y-%m-%d") if order.dt_applied else "???",
            )
        return "-"

    @admin.display(description=_("Цена"))
    def get_price(self, obj):
        return obj.order_item.purchase_price if obj.order_item else "-"

    @admin.display(description=_("Сумма"))
    def get_total(self, obj):
        return obj.order_item.purchase_total_price if obj.order_item else "-"


class SupplierPriceListForm(DocumentForm):
    class Meta:
        model = SupplierPriceList
        fields = "__all__"


class RetailPriceListForm(DocumentForm):
    class Meta:
        model = RetailPriceList
        fields = "__all__"


class PaymentOutItemInline(TabularInline):
    model = PaymentOutItem
    extra = 1
    # Фильтруем счета так же, как мы делали ранее:
    # только те, где есть неоплаченные айтемы для этой организации
    verbose_name = _("Оплачиваемый счет")
    verbose_name_plural = _("Распределение оплаты по счетам")


@admin.register(SupplierPriceList)
class SupplierPriceListAdmin(BaseDocumentAdmin):
    form = SupplierPriceListForm

    list_display = (
            "id",
            "supplier",
            "organization",
            "is_applied",
            "to_remove",
            "created"
        )
    list_display_links = ("id", "supplier")
    list_filter = ("is_applied", "to_remove", "supplier")
    search_fields = ("id", "supplier__last_name")
    inlines = [SupplierPriceItemInline]
    fields = BASE_FIELDS + ("supplier",)
    readonly_fields = BASE_READONLY


@admin.register(RetailPriceList)
class RetailPriceListAdmin(BaseDocumentAdmin):
    form = RetailPriceListForm
    list_display = (
        "id",
        "retail_store",
        "organization",
        "is_applied",
        "to_remove",
        "created",
    )
    list_display_links = ("id", "retail_store")
    list_filter = ("is_applied", "to_remove", "retail_store")
    search_fields = ("id", "retail_store__name")
    inlines = [RetailPriceItemInline]
    fields = BASE_FIELDS + ("retail_store", "comment")
    readonly_fields = BASE_READONLY

    class Media:
        js = ["documents/js/admin_retail_price_info.js"]


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

    list_filter = ["status", "is_applied", "retail_store"]
    search_fields = ["customer", "id"]
    readonly_fields = BASE_READONLY
    fields = BASE_FIELDS + ("customer", "retail_store", "status")
    inlines = [CustomeOrderItemInline]

    class Media:
        js = [
            "https://cdn.jsdelivr.net/npm/sweetalert2@11",
            "https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js",
            "documents/js/admin_price_fetch.js",
            "documents/js/admin_sortable_init.js",
        ]


class PurchaseInvoiceForm(DocumentForm):
    fill_from_orders = forms.BooleanField(
        label=_("Заполнить по выбранным заказам"),
        required=False,
        initial=False,
        help_text=_(
            "Автоматически добавит все недостающие PREPAID позиции из выбранных заказов"
        ),
    )

    class Meta:
        model = PurchaseInvoice
        fields = "__all__"


@admin.register(PurchaseInvoice)
class PurchaseInvoiceAdmin(BaseDocumentAdmin):
    form = PurchaseInvoiceForm
    list_filter = ("is_applied", "supplier")
    readonly_fields = BASE_READONLY
    fields = BASE_FIELDS + ("supplier", "bank_account", "orders", "fill_from_orders")
    filter_horizontal = ("orders",)
    conditional_fields = {
        **BaseDocumentAdmin.conditional_fields,
        "fill_from_orders": "is_applied == false",
    }

    inlines = [InvoiceItemInline]

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
                    kwargs["queryset"] = ContractorBankAccount.objects.filter(
                        contractor=invoice.supplier
                    )
                else:
                    kwargs["queryset"] = ContractorBankAccount.objects.none()
            else:
                # При создании нового документа поставщик еще не выбран в БД.
                # Список счетов будет пуст до первого сохранения.
                kwargs["queryset"] = ContractorBankAccount.objects.none()

        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        """
        Фильтр заказов-оснований:
        - Поставщик/Холдинг
        - Статус 'Проведен'
        - Наличие айтемов PREPAID без счета
        - Организация айтемов совпадает с организацией счета
        """
        if db_field.name == "orders":
            object_id = request.resolver_match.kwargs.get("object_id")
            if object_id:
                invoice = self.get_object(request, object_id)
                if invoice and invoice.supplier:
                    # 1. Фильтр по поставщику
                    vendor_query = Q(supplier=invoice.supplier)
                    if invoice.supplier.parent_holding:
                        vendor_query |= Q(supplier=invoice.supplier.parent_holding)

                    # 2. Фильтр по айтемам с учетом организации
                    # Нам нужны заказы, где есть хотя бы один айтем:
                    # - со способом PREPAID
                    # - без привязанного счета
                    # - организация которого совпадает с организацией счета (если она там указана)

                    item_filters = Q(
                        items__payment_method_purchase=OrderItem.PaymentMethod.PREPAID,
                        items__invoice_item__isnull=True,
                    )

                    # Если в счете указана организация, фильтруем заказы,
                    # в которых есть айтемы именно для этой организации
                    if invoice.organization:
                        item_filters &= Q(items__organization=invoice.organization)

                    kwargs["queryset"] = PurchaseOrder.objects.filter(
                        vendor_query, item_filters, is_applied=True
                    ).distinct()
                else:
                    kwargs["queryset"] = PurchaseOrder.objects.none()
            else:
                kwargs["queryset"] = PurchaseOrder.objects.none()

        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        # Сохраняем объект, чтобы ManyToMany связи (orders) пробросились в базу
        super().save_model(request, obj, form, change)

        if form.cleaned_data.get("fill_from_orders"):
            # Передаем request в наш вспомогательный метод
            self._fill_items_from_orders(request, obj)

    def _fill_items_from_orders(self, request, obj):
        """Логика автоматического наполнения позиций счета с учетом организации"""
        filters = Q(
            purchase_order__in=obj.orders.all(),
            payment_method_purchase=OrderItem.PaymentMethod.PREPAID,
            invoice_item__isnull=True,
        )

        if obj.organization:
            filters &= Q(organization=obj.organization)

        items_to_add = OrderItem.objects.filter(filters)

        created_count = 0
        for order_item in items_to_add:
            _, created = InvoiceItem.objects.get_or_create(
                invoice=obj,
                order_item=order_item,
                defaults={"sort_order": order_item.sort_order_purchase},
            )
            if created:
                created_count += 1

        # Теперь request здесь определен и сообщение сработает
        if created_count > 0:
            self.message_user(request, f"Добавлено позиций: {created_count}")
        else:
            self.message_user(
                request, "Новых позиций для добавления не найдено", level="WARNING"
            )


@admin.register(PaymentOrderOut)
class PaymentOrderOutAdmin(BaseDocumentAdmin):
    form = DocumentForm
    # fields = BASE_FIELDS + ("supplier", "bank_account", "total_debited")
    # Объединяем кортежи, чтобы не потерять системные поля из BaseDocumentAdmin
    fields = BASE_FIELDS[:-1] + (
        ("organization",),
        (
            "contractor",
            "contractor_bank_account",
        ),
        ("amount", "bank_commission", "total_debited"),
    )
    readonly_fields = BaseDocumentAdmin.readonly_fields + ("total_debited",)

    inlines = [PaymentOutItemInline]

    def save_model(self, request, obj, form, change):
        # Здесь в будущем можно добавить логику проверки:
        # сумма всех PaymentOutItem не должна превышать obj.amount
        super().save_model(request, obj, form, change)
