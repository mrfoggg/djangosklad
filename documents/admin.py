from decimal import Decimal
from urllib.parse import urlencode

from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.db.models import Count, DecimalField, F, OuterRef, Q, Subquery, Sum, Value, Window
from django.db.models.functions import Coalesce, RowNumber
from django.forms.models import BaseInlineFormSet
from django.urls import reverse
from django.utils.formats import number_format
from django.utils.timezone import localdate
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _
from djmoney.models.fields import MoneyField
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action
from unfold.widgets import (
    UnfoldAdminDecimalFieldWidget,
    UnfoldAdminMoneyWidget,
    UnfoldAdminSelectWidget,
    UnfoldAdminSplitDateTimeVerticalWidget,
)

from catalogs.models import ContractorBankAccount, OurBankAccount

from .models import (
    CustomerOrder,
    GoodsReceipt,
    GoodsReceiptItem,
    InvoiceItem,
    OrderItem,
    PaymentOrderOut,
    PaymentOutItem,
    PurchaseInvoice,
    PurchaseOrder,
    RetailPriceItem,
    RetailPriceList,
    SalesInvoice,
    SalesInvoiceItem,
    SupplierPriceItem,
    SupplierPriceList,
)

from .receipts import receipt_order_items
from .invoices import invoice_order_items, with_invoice_balance
from .order_lines import with_order_position
from .order_summary import supplier_order_summary
from .source_order_admin import (SourceOrderAdminMixin, SourceOrderFormSetMixin,
                                 SourceOrderOrganizationForm, source_order_organizations)


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


class ProductUnitSelectWidget(UnfoldAdminSelectWidget):
    def create_option(
        self, name, value, label, selected, index, subindex=None, attrs=None
    ):
        option = super().create_option(
            name, value, label, selected, index, subindex=subindex, attrs=attrs
        )
        product = getattr(value, "instance", None)
        if product is not None and product.unit_id:
            option["attrs"]["data-quantity-decimal-places"] = (
                product.unit.decimal_places
            )
            option["attrs"]["data-unit-symbol"] = product.unit.symbol
        return option


class OrderItemInlineForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "product" in self.fields:
            self.fields["product"].queryset = self.fields[
                "product"
            ].queryset.select_related("unit")

        instance = getattr(self, "instance", None)
        quantity_field = self.fields.get("quantity")
        if quantity_field:
            decimal_places = 0
            if instance and instance.product_id:
                decimal_places = instance.product.unit.decimal_places

            quantity_field.widget.attrs["step"] = (
                "1" if decimal_places == 0 else f"{Decimal(1).scaleb(-decimal_places):f}"
            )
            if not self.is_bound and instance and instance.pk:
                self.initial["quantity"] = f"{instance.quantity:.{decimal_places}f}"

        if not instance or not instance.pk:
            return

        purchase_order_applied = bool(
            instance.purchase_order and instance.purchase_order.is_applied
        )
        customer_order_applied = bool(
            instance.customer_order and instance.customer_order.is_applied
        )

        locked_fields = set()

        if purchase_order_applied or customer_order_applied:
            locked_fields.update(
                {"product", "rrp", "quantity", "warehouse", "organization"}
            )

        if purchase_order_applied:
            locked_fields.update({"purchase_price", "customer_order"})

        if customer_order_applied:
            locked_fields.update(
                {"customer_price", "payment_method_customer", "purchase_order"}
            )

        for name in locked_fields:
            if name in self.fields:
                self.fields[name].disabled = True

    def clean_quantity(self):
        quantity = self.cleaned_data.get("quantity")
        product = self.cleaned_data.get("product")
        if quantity is None or product is None:
            return quantity

        decimal_places = product.unit.decimal_places
        normalized_quantity = quantity.normalize()
        actual_decimal_places = max(0, -normalized_quantity.as_tuple().exponent)
        if actual_decimal_places > decimal_places:
            raise forms.ValidationError(
                _(
                    "Для единицы измерения '%(unit)s' разрешено знаков после "
                    "запятой: %(decimal_places)s."
                ),
                params={
                    "unit": product.unit.symbol,
                    "decimal_places": decimal_places,
                },
            )
        return quantity

    class Meta:
        widgets = {
            "product": ProductUnitSelectWidget(
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
        "position_number",
        "sort_order_purchase",
        "product",
        "purchase_price",
        "rrp",
        "quantity",
        "received_quantity",
        "remaining_quantity",
        "purchase_total_price",
        "organization",
        "customer_order",
        "warehouse",
        "get_invoice_link",
    )
    ordering = ("sort_order_purchase", "pk")
    readonly_fields = ("position_number", "purchase_total_price", "get_invoice_link", "received_quantity", "remaining_quantity")

    @admin.display(description=_("№"))
    def position_number(self, obj):
        return format_html('<span class="purchase-position-number">{}</span>', getattr(obj, "calculated_position_number", "—"))

    def get_queryset(self, request):
        received = GoodsReceiptItem.objects.filter(
            order_item_id=OuterRef("pk"), receipt__is_applied=True,
        ).order_by().values("order_item_id").annotate(total=Sum("quantity"))
        return super().get_queryset(request).annotate(
            calculated_position_number=Window(
                expression=RowNumber(), partition_by=[F("purchase_order_id")],
                order_by=[F("sort_order_purchase").asc(), F("pk").asc()],
            ),
            calculated_received=Coalesce(
                Subquery(received.values("total")[:1]), Value(Decimal("0")),
                output_field=DecimalField(max_digits=14, decimal_places=6),
            ),
        )

    @admin.display(description=_("Получено"))
    def received_quantity(self, obj):
        return getattr(obj, "calculated_received", Decimal("0")) if obj and obj.pk else "—"

    @admin.display(description=_("Осталось получить"))
    def remaining_quantity(self, obj):
        if not obj or not obj.pk:
            return "—"
        return obj.quantity - getattr(obj, "calculated_received", Decimal("0"))

    @admin.display(description=_("Счета"))
    def get_invoice_link(self, obj):
        if not obj or not obj.pk:
            return "—"
        return format_html_join(
            ", ", '<a href="{}">Счёт №{}</a>',
            ((reverse("admin:documents_purchaseinvoice_change", args=[item.invoice_id]), item.invoice_id)
             for item in obj.invoice_items.all()),
        ) or "—"


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


class OrderLineUnitSelectWidget(UnfoldAdminSelectWidget):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(
            name, value, label, selected, index, subindex=subindex, attrs=attrs,
        )
        item = getattr(value, "instance", None)
        if item is not None:
            unit = item.product.unit
            option["attrs"]["data-quantity-decimal-places"] = unit.decimal_places
            option["attrs"]["data-unit-symbol"] = unit.symbol
        return option


def configure_order_line_quantity(form):
    field = form.fields["order_item"]
    field.queryset = with_order_position(field.queryset).select_related("product__unit", "purchase_order")
    item = None
    if form.instance.order_item_id:
        item = field.queryset.filter(pk=form.instance.order_item_id).first()
    places = item.product.unit.decimal_places if item else 0
    form.fields["quantity"].widget.attrs["step"] = f"{Decimal(1).scaleb(-places):f}"
    quantity = form.instance.quantity
    if not form.is_bound and form.instance.pk and quantity is not None:
        # Never round away precision errors in existing data.
        rounded = quantity.quantize(Decimal(1).scaleb(-places))
        if rounded == quantity:
            form.initial["quantity"] = f"{quantity:.{places}f}"


class PurchaseInvoiceItemInlineForm(forms.ModelForm):
    class Meta:
        model = InvoiceItem
        fields = "__all__"
        widgets = {"order_item": OrderLineUnitSelectWidget()}

    def __init__(self, *args, invoice_context=None, order_ids=(), **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields.get("order_item")
        if field:
            field.label_from_instance = self.label_for_purchase
            if invoice_context is not None:
                available = invoice_order_items(invoice_context, order_ids)
                # Existing links stay visible; the formset validates their context.
                field.queryset = with_invoice_balance(
                    OrderItem.objects.filter(
                        Q(pk__in=available.filter(invoice_remaining__gt=0).values("pk"))
                        | Q(pk=self.instance.order_item_id)
                    ).select_related("product__unit", "purchase_order"), invoice_context.pk,
                )
            elif not self.instance.pk:
                field.queryset = with_invoice_balance(field.queryset).filter(invoice_remaining__gt=0)
            if self.instance.pk:
                field.disabled = True
        self.fields["quantity"].help_text = _("Пустое поле — оставшееся количество по заказу.")
        configure_order_line_quantity(self)

    def clean(self):
        data = super().clean()
        item = data.get("order_item")
        if item and data.get("quantity") is None and "quantity" not in self.errors:
            data["quantity"] = with_invoice_balance(
                OrderItem.objects.filter(pk=item.pk), self.instance.invoice_id,
            ).get().invoice_remaining
            if data["quantity"] <= 0:
                self.add_error("quantity", _("По строке заказа не осталось количества для счёта."))
        return data

    def label_for_purchase(self, obj):
        number = obj.purchase_order_id or "—"
        remaining = getattr(obj, "invoice_remaining", obj.quantity)
        return f"Заказ №{number} | Строка №{obj.order_position_number} | {obj.product.name} | Осталось {remaining:f} {obj.product.unit.symbol}"


class InvoiceItemFormSet(SourceOrderFormSetMixin, BaseInlineFormSet):
    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        context = PurchaseInvoice(pk=self.instance.pk, organization_id=None)
        for field in ("supplier", "organization"):
            value = self.data.get(field) if self.is_bound else getattr(self.instance, f"{field}_id")
            try:
                value = int(value) if value else None
            except (ValueError, TypeError):
                value = None
            setattr(context, f"{field}_id", value)
        # Ignore invalid suppliers here: the parent form reports the error.
        if context.supplier_id and not context._meta.get_field("supplier").remote_field.model.objects.filter(pk=context.supplier_id).exists():
            context.supplier_id = None
        kwargs.update(invoice_context=context, order_ids=self.selected_order_ids())
        return kwargs

    def selected_order_ids(self):
        if self.is_bound:
            return [int(value) for value in self.data.getlist("orders") if value.isdigit()]
        return list(self.instance.orders.values_list("pk", flat=True)) if self.instance.pk else getattr(self.instance, "_selected_order_ids", [])

    def clean(self):
        super().clean()
        self.pending_items = []
        if any(self.errors) or not self.instance.supplier_id or not self.instance.organization_id:
            return
        ids = self.selected_order_ids()
        list(OrderItem.objects.select_for_update().filter(purchase_order_id__in=ids).order_by("pk").values_list("pk", flat=True))
        available = {item.pk: item for item in invoice_order_items(self.instance, ids)}
        used = set()
        for form in self.forms:
            data = form.cleaned_data
            if not data or not data.get("order_item"):
                continue
            pk = data["order_item"].pk
            used.add(pk)
            if data.get("DELETE"):
                continue
            item = available.get(pk)
            if item is None:
                form.add_error("order_item", _("Строка не соответствует выбранным заказам, поставщику или организации счёта."))
            elif data["quantity"] > item.invoice_remaining:
                form.add_error("quantity", _("Доступно для счёта: %(quantity)s.") % {"quantity": item.invoice_remaining})
        if any(self.errors):
            return
        if self.data.get("fill_from_orders"):
            for item in available.values():
                if item.pk in used or item.invoice_remaining <= 0:
                    continue
                row = InvoiceItem(invoice=self.instance, order_item=item,
                                  quantity=item.invoice_remaining, sort_order=item.sort_order_purchase)
                try:
                    row.full_clean(exclude=("invoice",))
                except forms.ValidationError as error:
                    raise forms.ValidationError(
                        _("Не удалось заполнить товар %(product)s: %(error)s"),
                        params={"product": item.product, "error": "; ".join(error.messages)},
                    ) from error
                self.pending_items.append(row)

    def save_new_objects(self, commit=True):
        objects = super().save_new_objects(commit=commit)
        for item in self.pending_items:
            item.invoice = self.instance
            if commit:
                item.save()
            objects.append(item)
        return objects


class InvoiceItemInline(TabularInline):
    model = InvoiceItem
    form = PurchaseInvoiceItemInlineForm
    formset = InvoiceItemFormSet
    extra = 0
    tab = True

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        if "sort_order" in formset.form.base_fields:
            formset.form.base_fields["sort_order"].label = "⇅"
        return formset

    # Добавляем get_order_link в список полей
    fields = ("sort_order", "get_order_link", "order_item", "quantity", "get_price", "get_total")
    readonly_fields = ("get_order_link", "get_price", "get_total")

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
        return obj.total_price if obj and obj.pk else "—"


class SalesInvoiceItemInlineForm(forms.ModelForm):
    class Meta:
        model = SalesInvoiceItem
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "order_item" not in self.fields:
            return

        order_item_field = self.fields["order_item"]
        order_item_field.label_from_instance = self.label_for_customer
        if self.instance and self.instance.pk:
            order_item_field.disabled = True
            order_item_field.queryset = order_item_field.queryset.filter(
                pk=self.instance.order_item_id
            )
        else:
            order_item_field.queryset = order_item_field.queryset.filter(
                sales_invoice_item__isnull=True
            )

    def label_for_customer(self, obj):
        order_no = obj.customer_order.id if obj.customer_order else "???"
        order_dt = obj.customer_order.dt_applied if obj.customer_order else "???"
        return (
            f"№{order_no} от {order_dt} | {obj.product.name} "
            f"({obj.quantity} {obj.product.unit.symbol})"
        )


class SalesInvoiceItemInline(TabularInline):
    model = SalesInvoiceItem
    form = SalesInvoiceItemInlineForm
    extra = 0
    tab = True
    fields = ("sort_order", "get_order_link", "order_item", "get_price", "get_total")
    readonly_fields = ("get_order_link", "get_price", "get_total")

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        if "sort_order" in formset.form.base_fields:
            formset.form.base_fields["sort_order"].label = "⇅"
        return formset

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "order_item":
            object_id = request.resolver_match.kwargs.get("object_id")
            invoice = (
                SalesInvoice.objects.filter(pk=object_id).first()
                if object_id
                else None
            )
            if invoice:
                selected_order_ids = invoice.orders.values_list("id", flat=True)
                item_filters = Q(
                    customer_order_id__in=selected_order_ids,
                    payment_method_customer=(
                        OrderItem.CustomerPaymentMethod.PREPAID
                    ),
                )
                if invoice.organization:
                    item_filters &= Q(organization=invoice.organization)
                availability_filter = Q(sales_invoice_item__isnull=True) | Q(
                    sales_invoice_item__invoice=invoice
                )
                kwargs["queryset"] = OrderItem.objects.filter(
                    item_filters, availability_filter
                ).distinct()
            else:
                kwargs["queryset"] = OrderItem.objects.none()

        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(description=_("Заказ"))
    def get_order_link(self, obj):
        if obj.order_item and obj.order_item.customer_order:
            order = obj.order_item.customer_order
            url = reverse("admin:documents_customerorder_change", args=[order.id])
            return format_html(
                '<a href="{}" target="_blank" style="font-weight: 600; '
                'color: #3b82f6; text-decoration: underline;">{}/{}</a>',
                url,
                order.id,
                order.dt_applied.strftime("%Y-%m-%d")
                if order.dt_applied
                else "???",
            )
        return "-"

    @admin.display(description=_("Цена"))
    def get_price(self, obj):
        return obj.order_item.customer_price if obj.order_item else "-"

    @admin.display(description=_("Сумма"))
    def get_total(self, obj):
        return obj.order_item.customer_total_price if obj.order_item else "-"


class SupplierPriceListForm(DocumentForm):
    class Meta:
        model = SupplierPriceList
        fields = "__all__"


class RetailPriceListForm(DocumentForm):
    class Meta:
        model = RetailPriceList
        fields = "__all__"


class PaymentInvoiceSelectWidget(UnfoldAdminSelectWidget):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(
            name, value, label, selected, index, subindex=subindex, attrs=attrs
        )
        invoice = getattr(value, "instance", None)
        if invoice is not None:
            option["attrs"]["data-supplier-id"] = invoice.supplier_id
            option["attrs"]["data-organization-id"] = invoice.organization_id
        return option


class PaymentOutItemInlineForm(forms.ModelForm):
    def __init__(self, *args, payment=None, **kwargs):
        self.payment = payment
        super().__init__(*args, **kwargs)
        invoice_field = self.fields.get("invoice")
        if invoice_field:
            invoice_field.queryset = invoice_field.queryset.annotate(
                calculated_total=Coalesce(
                    Sum(F("items__quantity") * F("items__order_item__purchase_price")),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=20, decimal_places=2),
                )
            )
            paid = PaymentOutItem.objects.filter(
                invoice_id=OuterRef("pk"), payment__is_applied=True
            )
            if payment is not None and payment.pk:
                paid = paid.exclude(payment_id=payment.pk)
            paid = paid.order_by().values("invoice_id").annotate(total=Sum("amount"))
            invoice_field.queryset = invoice_field.queryset.annotate(
                calculated_paid=Coalesce(
                    Subquery(paid.values("total")[:1]), Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=20, decimal_places=2),
                )
            ).filter(
                Q(calculated_total__gt=F("calculated_paid"))
                | Q(pk=self.instance.invoice_id)
            )
            invoice_field.label_from_instance = self.invoice_label

    def clean_invoice(self):
        invoice = self.cleaned_data["invoice"]
        if self.payment is not None and (
            invoice.supplier_id != self.payment.contractor_id
            or invoice.organization_id != self.payment.organization_id
        ):
            raise forms.ValidationError(
                _("Счет должен принадлежать контрагенту и организации платежа.")
            )
        return invoice

    @staticmethod
    def invoice_label(invoice):
        total = number_format(invoice.calculated_total, decimal_pos=2, use_l10n=True)
        return _("%(invoice)s — %(total)s грн") % {
            "invoice": invoice,
            "total": total,
        }

    class Meta:
        model = PaymentOutItem
        fields = "__all__"
        widgets = {"invoice": PaymentInvoiceSelectWidget()}
        labels = {"sort_order": "⇅"}


class PaymentOutItemInlineFormSet(BaseInlineFormSet):
    # Amounts, including zero, have already been resolved by clean().
    def save_new(self, form, commit=True):
        obj = super().save_new(form, commit=False)
        if commit:
            obj.save(resolve_amount=False)
        return obj

    def save_existing(self, form, instance, commit=True):
        obj = super().save_existing(form, instance, commit=False)
        if commit:
            obj.save(resolve_amount=False)
        return obj

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        # The parent form is validated after formset construction in Django admin.
        payment = PaymentOrderOut(pk=self.instance.pk, organization_id=None)
        for field in ("contractor", "organization"):
            value = self.data.get(field) if self.is_bound else getattr(
                self.instance, f"{field}_id", None
            )
            try:
                value = int(value) if value else None
            except (TypeError, ValueError):
                value = None
            setattr(payment, f"{field}_id", value)
        kwargs["payment"] = payment
        return kwargs

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        allocated = Decimal("0.00")
        balances = {}
        for form in self.forms:
            data = form.cleaned_data
            if not data or data.get("DELETE"):
                continue
            invoice = data.get("invoice")
            if invoice is None:
                continue
            if (
                invoice.supplier_id != self.instance.contractor_id
                or invoice.organization_id != self.instance.organization_id
            ):
                form.add_error("invoice", _("Счет должен принадлежать контрагенту и организации платежа."))
                continue
            amount = data.get("amount")
            if amount is not None and amount < 0:
                form.add_error("amount", _("Сумма оплаты не может быть отрицательной."))
                continue
            if invoice.pk not in balances:
                total = invoice.items.aggregate(
                    total=Sum(F("quantity") * F("order_item__purchase_price"))
                )["total"] or Decimal("0.00")
                paid = PaymentOutItem.objects.filter(
                    invoice=invoice, payment__is_applied=True
                )
                if self.instance.pk:
                    paid = paid.exclude(payment_id=self.instance.pk)
                paid_total = paid.aggregate(total=Sum("amount"))["total"]
                balances[invoice.pk] = total - (paid_total or Decimal("0.00"))
            if amount is None or amount == 0:
                amount = max(balances[invoice.pk], Decimal("0.00"))
            balances[invoice.pk] -= amount
            data["amount"] = amount
            form.instance.amount = amount
            allocated += amount
        if self.instance.amount is not None and allocated > self.instance.amount:
            raise forms.ValidationError(
                _("Распределено по счетам %(allocated)s грн — больше суммы платежа %(amount)s грн."),
                params={"allocated": allocated, "amount": self.instance.amount},
            )


class PaymentOutItemInline(TabularInline):
    model = PaymentOutItem
    form = PaymentOutItemInlineForm
    formset = PaymentOutItemInlineFormSet
    fields = ("sort_order", "invoice", "amount", "payment_status")
    ordering = ("sort_order", "pk")
    extra = 1
    readonly_fields = ("payment_status",)
    verbose_name = _("Оплачиваемый счет")
    verbose_name_plural = _("Распределение оплаты по счетам")

    def get_queryset(self, request):
        decimal_field = DecimalField(max_digits=20, decimal_places=2)
        invoice_total = (
            InvoiceItem.objects.filter(invoice_id=OuterRef("invoice_id"))
            .values("invoice_id")
            .annotate(total=Sum(F("quantity") * F("order_item__purchase_price")))
            .values("total")[:1]
        )
        paid_total = (
            PaymentOutItem.objects.filter(
                invoice_id=OuterRef("invoice_id"),
                payment__is_applied=True,
            )
            .values("invoice_id")
            .annotate(total=Sum("amount"))
            .values("total")[:1]
        )
        return super().get_queryset(request).select_related("payment").annotate(
            calculated_invoice_total=Coalesce(
                Subquery(invoice_total, output_field=decimal_field),
                Value(Decimal("0.00")),
                output_field=decimal_field,
            ),
            calculated_paid_total=Coalesce(
                Subquery(paid_total, output_field=decimal_field),
                Value(Decimal("0.00")),
                output_field=decimal_field,
            ),
        )

    @admin.display(description=_("Состояние оплаты"))
    def payment_status(self, obj):
        if not obj or not obj.pk or not obj.invoice_id:
            return "—"

        invoice_total = getattr(obj, "calculated_invoice_total", Decimal("0.00"))
        paid_total = getattr(obj, "calculated_paid_total", Decimal("0.00"))
        if not obj.payment.is_applied:
            paid_total += obj.amount or Decimal("0.00")

        difference = paid_total - invoice_total
        if difference < 0:
            amount = number_format(-difference, decimal_pos=2, use_l10n=True)
            return format_html(
                '<span class="text-orange-600 dark:text-orange-400">{}</span>',
                _("Недоплата: %(amount)s грн") % {"amount": amount},
            )
        if difference > 0:
            amount = number_format(difference, decimal_pos=2, use_l10n=True)
            return format_html(
                '<span class="text-red-600 dark:text-red-400">{}</span>',
                _("Переплата: %(amount)s грн") % {"amount": amount},
            )
        return format_html(
            '<span class="text-green-600 dark:text-green-400">{}</span>',
            _("Оплачено"),
        )


class OurBankAccountSelectWidget(UnfoldAdminSelectWidget):
    def create_option(
        self, name, value, label, selected, index, subindex=None, attrs=None
    ):
        option = super().create_option(
            name, value, label, selected, index, subindex=subindex, attrs=attrs
        )
        account = getattr(value, "instance", None)
        if account is not None:
            option["attrs"]["data-organization-id"] = account.organization_id
            option["attrs"]["data-is-default"] = str(account.is_default).lower()
        return option


class ContractorBankAccountSelectWidget(UnfoldAdminSelectWidget):
    def create_option(
        self, name, value, label, selected, index, subindex=None, attrs=None
    ):
        option = super().create_option(
            name, value, label, selected, index, subindex=subindex, attrs=attrs
        )
        account = getattr(value, "instance", None)
        if account is not None:
            option["attrs"]["data-contractor-id"] = account.contractor_id
            option["attrs"]["data-is-primary"] = str(
                account.contractor.primary_account_id == account.pk
            ).lower()
        return option


class PaymentOrderOutForm(DocumentForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        account_field = self.fields.get("our_bank_account")
        if account_field:
            account_field.queryset = account_field.queryset.select_related(
                "organization"
            )
        contractor_account_field = self.fields.get("contractor_bank_account")
        if contractor_account_field:
            contractor_account_field.queryset = (
                contractor_account_field.queryset.select_related("contractor")
            )

    class Meta:
        model = PaymentOrderOut
        fields = "__all__"
        widgets = {
            "our_bank_account": OurBankAccountSelectWidget(),
            "contractor_bank_account": ContractorBankAccountSelectWidget(),
        }


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
    def clean(self):
        data = super().clean()
        self.instance._force_current_date = bool(data.get("force_current_date"))
        return data

    class Meta:
        model = PurchaseOrder
        fields = "__all__"


class OrderTotalsAdminMixin:
    total_field = None
    quantity_field = "items__quantity"
    product_field = "items__product"

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        decimal_output = DecimalField(max_digits=20, decimal_places=2)
        return queryset.annotate(
            calculated_total=Coalesce(
                Sum(self.total_field),
                Value(Decimal("0.00")),
                output_field=decimal_output,
            ),
            calculated_quantity=Coalesce(
                Sum(self.quantity_field),
                Value(Decimal("0.00")),
                output_field=decimal_output,
            ),
            calculated_product_count=Count(self.product_field, distinct=True),
        )

    @admin.display(description=_("Итого, грн"), ordering="calculated_total")
    def order_total(self, obj):
        total = getattr(obj, "calculated_total", Decimal("0.00"))
        return number_format(total, decimal_pos=2, use_l10n=True)

    @admin.display(description=_("Количество"), ordering="calculated_quantity")
    def order_quantity(self, obj):
        return getattr(obj, "calculated_quantity", Decimal("0.00"))

    @admin.display(
        description=_("Наименований"), ordering="calculated_product_count"
    )
    def product_count(self, obj):
        return getattr(obj, "calculated_product_count", 0)


# Заказ поставщику
@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(OrderTotalsAdminMixin, BaseDocumentAdmin):
    form = PurchaseOrderForm
    total_field = "items__purchase_total_price"
    readonly_fields = BASE_READONLY + (
        "order_total",
        "order_quantity",
        "product_count",
        "linked_invoices", "linked_receipts", "linked_customer_orders", "delivery_summary", "payment_summary",
    )
    list_display = (
        "id",
        "supplier",
        "organization",
        "order_total",
        "order_quantity",
        "product_count",
        "is_applied",
        "created",
    )
    list_display_links = ("id", "supplier")
    list_filter = ("is_applied", "supplier")
    inlines = [PurchaseOrderItemInline]

    fields = BASE_FIELDS + (
        "supplier", "price_type", "comment",
        ("order_total", "order_quantity", "product_count"),
        "linked_invoices", "linked_receipts", "linked_customer_orders", "delivery_summary", "payment_summary",
    )

    @staticmethod
    def _summary(obj):
        if not hasattr(obj, "_supplier_order_summary"):
            obj._supplier_order_summary = supplier_order_summary(obj)
        return obj._supplier_order_summary

    @staticmethod
    def _money(value):
        if value is None:
            return _("Не определено: не заполнены цены")
        return _("%(amount)s грн") % {"amount": number_format(value, decimal_pos=2, use_l10n=True)}

    def _document_links(self, documents, model_name, number_field, date_field):
        entries = []
        for document in documents:
            number = getattr(document, number_field) or "—"
            date = getattr(document, date_field)
            if date:
                date_label = _("Дата документа поставщика: %(date)s") % {"date": f"{date:%d.%m.%Y}"}
            elif document.is_applied and document.dt_applied:
                date_label = _("Проведён: %(date)s") % {"date": f"{localdate(document.dt_applied):%d.%m.%Y}"}
            else:
                date_label = _("Создан: %(date)s") % {"date": f"{localdate(document.created):%d.%m.%Y}"}
            label = f"№{document.pk} · Документ поставщика: {number}"
            if date or not (document.is_applied and document.dt_applied):
                status = _("Проведён") if document.is_applied else _("Черновик")
                date_label = f"{date_label} · {status}"
            details = _("Сумма документа: %(amount)s") % {"amount": self._money(document.summary_total)}
            if model_name == "purchaseinvoice":
                paid = document.summary_paid
                total = document.summary_total
                if total is not None and paid == total:
                    payment_status = _("Оплачен")
                else:
                    payment_status = _("Оплачено: %(amount)s") % {"amount": self._money(paid)}
                    if total is None:
                        payment_status += _(" · Остаток не определён: не заполнены цены")
                    elif paid < total:
                        payment_status += _(" · Не оплачено: %(amount)s") % {"amount": self._money(total - paid)}
                    else:
                        payment_status += _(" · Переплата: %(amount)s") % {"amount": self._money(paid - total)}
                details += f" · {payment_status}"
            entries.append((reverse(f"admin:documents_{model_name}_change", args=[document.pk]),
                            label, document.supplier, date_label, details))
        return format_html_join(
            "", '<div class="mb-3"><div><a href="{}">{}</a> · {} · {}</div><div>{}</div></div>', entries,
        ) or "—"

    @admin.display(description=_("Связанные счета"))
    def linked_invoices(self, obj):
        if not obj or not obj.pk:
            return "—"
        return self._document_links(self._summary(obj)["invoices"], "purchaseinvoice",
                                    "supplier_invoice_number", "supplier_invoice_date")

    @admin.display(description=_("Связанные поступления"))
    def linked_receipts(self, obj):
        if not obj or not obj.pk:
            return "—"
        return self._document_links(self._summary(obj)["receipts"], "goodsreceipt",
                                    "supplier_delivery_note_number", "supplier_delivery_note_date")

    @admin.display(description=_("Связанные заказы покупателей"))
    def linked_customer_orders(self, obj):
        if not obj or not obj.pk:
            return "—"
        orders = CustomerOrder.objects.filter(
            pk__in=obj.items.values("customer_order_id"),
        ).select_related("customer").prefetch_related("items").order_by("pk")
        entries = []
        for order in orders:
            items = list(order.items.all())
            totals = {}
            for price_field in ("purchase_price", "customer_price"):
                totals[price_field] = None if any(getattr(item, price_field) is None for item in items) else sum(
                    (item.quantity * getattr(item, price_field) for item in items), Decimal("0"),
                )
            quantity = sum((item.quantity for item in items), Decimal("0"))
            quantity_places = max(0, -quantity.normalize().as_tuple().exponent)
            product_count = len({item.product_id for item in items})
            entries.append((
                reverse("admin:documents_customerorder_change", args=[order.pk]),
                f"№{order.pk}", order.customer,
                f"{localdate(order.created):%d.%m.%Y}",
                _("Проведён") if order.is_applied else _("Черновик"),
                number_format(quantity, decimal_pos=quantity_places, use_l10n=True), product_count,
                self._money(totals["purchase_price"]), self._money(totals["customer_price"]),
            ))
        return format_html_join(
            "", '<div class="mb-3"><div><a href="{}">{}</a> · {} · Создан: {} · {}</div>'
            '<div>Количество товаров: {} · Наименований: {}</div>'
            '<div>Сумма закупки: {} · Сумма продажи: {}</div></div>', entries,
        ) or "—"

    @admin.display(description=_("Итоги поставки"))
    def delivery_summary(self, obj):
        if not obj or not obj.pk:
            return "—"
        summary = self._summary(obj)
        rows = []
        for kind, label in (("ordered", _("Заказано")), ("received", _("Поставлено")), ("pending", _("Ожидается"))):
            quantities = "; ".join(
                f'{number_format(group[kind], decimal_pos=group["places"], use_l10n=True)} {group["symbol"]}'
                for group in summary["quantities"]
            ) or "0"
            rows.append((label, quantities, self._money(summary["amounts"][kind])))
        return format_html(
            '{}<div>Учитываются только проведённые поступления.</div>',
            format_html_join("", '<div><strong>{}:</strong> {} · {}</div>', rows),
        )

    @admin.display(description=_("Итоги оплаты заказа"))
    def payment_summary(self, obj):
        if not obj or not obj.pk:
            return "—"
        summary = self._summary(obj)
        balance = summary["balance"]
        if not summary["quantities"]:
            status = _("В заказе нет позиций")
        elif balance is None:
            status = _("Статус оплаты не определён")
        elif balance > 0:
            status = _("Не оплачено по заказу: %(amount)s") % {"amount": self._money(balance)}
        elif balance < 0:
            status = _("Переплата по заказу: %(amount)s") % {"amount": self._money(-balance)}
        else:
            status = _("Заказ оплачен полностью")
        note = _("Учтены только проведённые платежи, распределённые по связанным счетам. Комиссия банка не учитывается.")
        if summary["estimated"]:
            note += _(" Для общих счетов оплата заказа рассчитана пропорционально стоимости его строк в счёте.")
        paid = self._money(summary["paid"]) if summary["paid"] is not None else _("Не удалось распределить оплату общего счёта")
        return format_html('<div><strong>Оплачено{}:</strong> {}</div><div>{}</div><div>{}</div>',
                           _(" (расчётно)") if summary["estimated"] else "", paid, status, note)

    actions_detail = [{
        "title": _("Создать на основании"), "icon": "add_circle",
        "items": ["create_supplier_invoice", "create_goods_receipt"],
    }]

    def _can_create_from_order(self, request, object_id, target):
        order = self.get_object(request, object_id) if object_id else None
        return bool(order and order.is_applied and not order.to_remove
                    and self.has_view_permission(request, order)
                    and self.admin_site._registry[target].has_add_permission(request))

    def has_create_supplier_invoice_permission(self, request, object_id=None):
        return self._can_create_from_order(request, object_id, PurchaseInvoice)

    def has_create_goods_receipt_permission(self, request, object_id=None):
        return self._can_create_from_order(request, object_id, GoodsReceipt)

    def _create_from_order(self, request, object_id, target):
        order = self.get_object(request, object_id)
        if order is None:
            raise Http404
        if not self._can_create_from_order(request, object_id, target):
            raise PermissionDenied
        organizations = source_order_organizations(order)
        params = {"from_order": order.pk}
        if organizations.count() > 1:
            form = SourceOrderOrganizationForm(request, order, data=request.POST or None)
            if not form.is_valid():
                return TemplateResponse(request, "unfold/helpers/dialog.html", {
                    "dialog": self.source_organization_dialog,
                    "form": form, "form_submit_text": _("Продолжить"),
                })
            params["organization"] = form.cleaned_data["organization"].pk
        url = reverse(f"admin:documents_{target._meta.model_name}_add")
        url = f"{url}?{urlencode(params)}"
        if request.headers.get("HX-Request") == "true":
            return HttpResponse(headers={"HX-Redirect": url})
        return HttpResponseRedirect(url)

    @action(description=_("Счёт поставщика"), permissions=["create_supplier_invoice"])
    def create_supplier_invoice(self, request, object_id):
        return self._create_from_order(request, object_id, PurchaseInvoice)

    @action(description=_("Поступление товаров"), permissions=["create_goods_receipt"])
    def create_goods_receipt(self, request, object_id):
        return self._create_from_order(request, object_id, GoodsReceipt)

    source_organization_dialog = {
        "title": _("Выберите организацию"),
        "description": _("Для какой организации создать документ?"),
    }
    # Set UI metadata separately: the handler skips the dialog for a single organization.
    create_supplier_invoice.dialog = source_organization_dialog
    create_goods_receipt.dialog = source_organization_dialog

    class Media:
        js = [
            "https://cdn.jsdelivr.net/npm/sweetalert2@11",
            "https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js",
            "documents/js/admin_price_fetch.js?v=2",
            "documents/js/admin_quantity_step.js",
            "documents/js/admin_sortable_init.js?v=2",
        ]


class CustomerOrderForm(DocumentForm):
    class Meta:
        model = CustomerOrder
        fields = "__all__"


# Заказ покупателя
@admin.register(CustomerOrder)
class CustomerOrderAdmin(OrderTotalsAdminMixin, BaseDocumentAdmin):
    form = CustomerOrderForm
    total_field = "items__customer_total_price"
    list_display = (
        "id",
        "customer",
        "retail_store",
        "organization",
        "order_total",
        "order_quantity",
        "product_count",
        "status",
        "is_applied",
        "created",
    )
    list_display_links = ("id", "customer")

    list_filter = ["status", "is_applied", "retail_store"]
    search_fields = ["customer", "id"]
    readonly_fields = BASE_READONLY + (
        "order_total",
        "order_quantity",
        "product_count",
    )
    fields = BASE_FIELDS + (
        "customer",
        "retail_store",
        "status",
        ("order_total", "order_quantity", "product_count"),
    )
    inlines = [CustomeOrderItemInline]

    class Media:
        js = [
            "https://cdn.jsdelivr.net/npm/sweetalert2@11",
            "https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js",
            "documents/js/admin_price_fetch.js?v=2",
            "documents/js/admin_quantity_step.js",
            "documents/js/admin_sortable_init.js?v=2",
        ]


class PurchaseInvoiceForm(DocumentForm):
    fill_from_orders = forms.BooleanField(
        label=_("Заполнить остатками по заказам"),
        required=False,
        initial=False,
        help_text=_(
            "При сохранении добавит отсутствующие строки с оставшимся количеством из выбранных заказов. Учитываются только проведённые счета; уже введённые строки сохранятся."
        ),
    )

    class Meta:
        model = PurchaseInvoice
        fields = "__all__"


@admin.register(PurchaseInvoice)
class PurchaseInvoiceAdmin(SourceOrderAdminMixin, OrderTotalsAdminMixin, BaseDocumentAdmin):
    form = PurchaseInvoiceForm
    total_field = F("items__quantity") * F("items__order_item__purchase_price")
    quantity_field = "items__quantity"
    product_field = "items__order_item__product"
    list_display = (
        "id",
        "supplier_invoice_number",
        "supplier_invoice_date",
        "supplier",
        "organization",
        "order_total",
        "order_quantity",
        "product_count",
        "is_applied",
        "created",
    )
    list_display_links = ("id", "supplier")
    list_filter = ("is_applied", "supplier")
    search_fields = ("supplier_invoice_number",)
    readonly_fields = BASE_READONLY + (
        "order_total",
        "order_quantity",
        "product_count",
    )
    fields = BASE_FIELDS + (
        ("supplier_invoice_number", "supplier_invoice_date"),
        "supplier",
        "bank_account",
        "orders",
        "fill_from_orders",
        ("order_total", "order_quantity", "product_count"),
    )
    filter_horizontal = ("orders",)
    conditional_fields = {
        **BaseDocumentAdmin.conditional_fields,
        "fill_from_orders": "is_applied == false",
    }

    inlines = [InvoiceItemInline]

    class Media:
        js = [
            "https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js",
            "documents/js/admin_sortable_init.js?v=2",
            "documents/js/admin_quantity_step.js",
        ]

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
        if db_field.name == "orders":
            # The item formset validates the supplier and organization, including POST data.
            kwargs["queryset"] = PurchaseOrder.objects.filter(is_applied=True, to_remove=False)
        return super().formfield_for_manytomany(db_field, request, **kwargs)


class SalesInvoiceForm(DocumentForm):
    fill_from_orders = forms.BooleanField(
        label=_("Заполнить по выбранным заказам"),
        required=False,
        initial=False,
        help_text=_(
            "Автоматически добавит доступные позиции выбранных заказов "
            "с видом оплаты «Оплата по счету»."
        ),
    )

    class Meta:
        model = SalesInvoice
        fields = "__all__"


@admin.register(SalesInvoice)
class SalesInvoiceAdmin(OrderTotalsAdminMixin, BaseDocumentAdmin):
    form = SalesInvoiceForm
    total_field = "items__order_item__customer_total_price"
    quantity_field = "items__order_item__quantity"
    product_field = "items__order_item__product"
    list_display = (
        "id",
        "customer",
        "organization",
        "order_total",
        "order_quantity",
        "product_count",
        "is_applied",
        "created",
    )
    list_display_links = ("id", "customer")
    list_filter = ("is_applied", "customer", "organization")
    search_fields = ("id", "customer__last_name")
    readonly_fields = BASE_READONLY + (
        "order_total",
        "order_quantity",
        "product_count",
    )
    fields = BASE_FIELDS + (
        "customer",
        "bank_account",
        "note",
        "orders",
        "fill_from_orders",
        ("order_total", "order_quantity", "product_count"),
    )
    filter_horizontal = ("orders",)
    conditional_fields = {
        **BaseDocumentAdmin.conditional_fields,
        "fill_from_orders": "is_applied == false",
    }
    inlines = [SalesInvoiceItemInline]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "bank_account":
            object_id = request.resolver_match.kwargs.get("object_id")
            invoice = self.get_object(request, object_id) if object_id else None
            if invoice and invoice.organization_id:
                kwargs["queryset"] = OurBankAccount.objects.filter(
                    organization=invoice.organization
                )
            else:
                kwargs["queryset"] = OurBankAccount.objects.none()

        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == "orders":
            object_id = request.resolver_match.kwargs.get("object_id")
            invoice = self.get_object(request, object_id) if object_id else None
            if invoice and invoice.customer_id:
                customer_query = Q(customer=invoice.customer)
                if invoice.customer.parent_holding_id:
                    customer_query |= Q(customer=invoice.customer.parent_holding)

                item_filters = Q(
                    items__payment_method_customer=(
                        OrderItem.CustomerPaymentMethod.PREPAID
                    )
                )
                if invoice.organization:
                    item_filters &= Q(items__organization=invoice.organization)

                availability_filter = Q(
                    items__sales_invoice_item__isnull=True
                ) | Q(items__sales_invoice_item__invoice=invoice)
                kwargs["queryset"] = CustomerOrder.objects.filter(
                    customer_query,
                    item_filters,
                    availability_filter,
                    is_applied=True,
                ).distinct()
            else:
                kwargs["queryset"] = CustomerOrder.objects.none()

        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if form.cleaned_data.get("fill_from_orders"):
            self._fill_items_from_orders(request, form.instance)

    def _fill_items_from_orders(self, request, obj):
        filters = Q(
            customer_order__in=obj.orders.all(),
            payment_method_customer=OrderItem.CustomerPaymentMethod.PREPAID,
            sales_invoice_item__isnull=True,
        )
        if obj.organization:
            filters &= Q(organization=obj.organization)

        created_count = 0
        for order_item in OrderItem.objects.filter(filters):
            _, created = SalesInvoiceItem.objects.get_or_create(
                invoice=obj,
                order_item=order_item,
                defaults={"sort_order": order_item.sort_order_customer},
            )
            if created:
                created_count += 1

        if created_count:
            self.message_user(request, f"Добавлено позиций: {created_count}")
        else:
            self.message_user(
                request, "Новых позиций для добавления не найдено", level="WARNING"
            )


@admin.register(PaymentOrderOut)
class PaymentOrderOutAdmin(BaseDocumentAdmin):
    form = PaymentOrderOutForm
    list_display = (
        "id",
        "payment_number",
        "contractor",
        "organization",
        "category",
        "amount",
        "allocated_amount",
        "payment_difference",
        "total_debited",
        "is_applied",
        "created",
    )
    list_display_links = ("id", "contractor")
    list_filter = ("category", "is_applied")
    search_fields = ("payment_number", "verification_code", "uetr")
    # fields = BASE_FIELDS + ("supplier", "bank_account", "total_debited")
    # Объединяем кортежи, чтобы не потерять системные поля из BaseDocumentAdmin
    fields = BASE_FIELDS[:-1] + (
        "payment_number",
        ("verification_code", "uetr"),
        "category",
        ("organization", "our_bank_account"),
        (
            "contractor",
            "contractor_bank_account",
        ),
        (
            "amount",
            "allocated_amount",
            "payment_difference",
            "bank_commission",
            "total_debited",
        ),
    )
    readonly_fields = BaseDocumentAdmin.readonly_fields + (
        "allocated_amount",
        "payment_difference",
        "total_debited",
    )

    inlines = [PaymentOutItemInline]
    conditional_fields = {
        **BaseDocumentAdmin.conditional_fields,
        "our_bank_account": "organization",
    }

    class Media:
        js = [
            "https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js",
            "documents/js/admin_payment_bank_accounts.js",
            "documents/js/admin_sortable_init.js?v=2",
        ]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            calculated_allocated_amount=Coalesce(
                Sum("paymentoutitem__amount"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=20, decimal_places=2),
            )
        )

    @admin.display(
        description=_("Распределено по счетам"),
        ordering="calculated_allocated_amount",
    )
    def allocated_amount(self, obj):
        amount = getattr(obj, "calculated_allocated_amount", Decimal("0.00"))
        return number_format(amount, decimal_pos=2, use_l10n=True)

    @admin.display(description=_("Расхождение"))
    def payment_difference(self, obj):
        payment_amount = getattr(obj, "amount", None) or Decimal("0.00")
        allocated_amount = getattr(
            obj, "calculated_allocated_amount", Decimal("0.00")
        )
        difference = payment_amount - allocated_amount
        formatted_difference = number_format(
            abs(difference), decimal_pos=2, use_l10n=True
        )

        if difference > 0:
            return _("Переплата: %(amount)s грн") % {"amount": formatted_difference}
        if difference < 0:
            return _("Недоплата: %(amount)s грн") % {"amount": formatted_difference}
        return _("Без расхождений")


class GoodsReceiptForm(DocumentForm):
    fill_from_order = forms.BooleanField(
        label=_("Заполнить остатками по заказам"), required=False,
        help_text=_("Добавит отсутствующие строки с неполученным количеством при сохранении. Уже введённые строки сохранятся."),
    )

    class Meta:
        model = GoodsReceipt
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["orders"].queryset = PurchaseOrder.objects.filter(
            is_applied=True, to_remove=False,
        )

    def clean(self):
        data = super().clean()
        self.instance._selected_order_ids = [order.pk for order in data.get("orders", [])]
        return data


class GoodsReceiptItemForm(forms.ModelForm):
    class Meta:
        model = GoodsReceiptItem
        fields = "__all__"
        labels = {"sort_order": "⇅"}
        widgets = {"order_item": OrderLineUnitSelectWidget()}

    def __init__(self, *args, receipt_context=None, order_ids=None, **kwargs):
        super().__init__(*args, **kwargs)
        if receipt_context is not None:
            self.fields["order_item"].queryset = receipt_order_items(receipt_context, order_ids).filter(
                Q(remaining_quantity__gt=0) | Q(pk=self.instance.order_item_id)
            )
        self.fields["order_item"].label_from_instance = self.label_for_order_line
        if self.instance.pk:
            self.fields["order_item"].disabled = True
        configure_order_line_quantity(self)

    @staticmethod
    def label_for_order_line(item):
        remaining = getattr(item, "remaining_quantity", item.quantity)
        return (
            f"Заказ №{item.purchase_order_id} | Строка №{item.order_position_number} | "
            f"{item.product} — осталось {remaining.normalize():f} {item.product.unit.symbol}"
        )


class GoodsReceiptItemFormSet(SourceOrderFormSetMixin, BaseInlineFormSet):
    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        context = GoodsReceipt(pk=self.instance.pk, organization_id=None)
        for field in ("supplier", "organization"):
            value = self.data.get(field) if self.is_bound else getattr(self.instance, f"{field}_id")
            try:
                value = int(value) if value else None
            except (TypeError, ValueError):
                value = None
            setattr(context, f"{field}_id", value)
        kwargs.update(receipt_context=context, order_ids=self.selected_order_ids())
        return kwargs

    def selected_order_ids(self):
        if self.is_bound:
            values = self.data.getlist("orders")
            return [int(value) for value in values if str(value).isdigit()]
        return list(self.instance.orders.values_list("pk", flat=True)) if self.instance.pk else getattr(self.instance, "_selected_order_ids", [])

    def clean(self):
        super().clean()
        self.pending_items = []
        if any(self.errors) or not self.instance.supplier_id or not self.instance.organization_id:
            return
        # Admin validates and saves the entire document inside a transaction.
        # Lock source rows before reading balances so concurrent receipts serialize.
        list(OrderItem.objects.select_for_update().filter(
            purchase_order_id__in=self.selected_order_ids(),
        ).order_by("pk").values_list("pk", flat=True))
        available = {item.pk: item for item in receipt_order_items(self.instance, self.selected_order_ids())}
        used = set()
        for form in self.forms:
            data = form.cleaned_data
            if not data or not data.get("order_item"):
                continue
            item_id = data["order_item"].pk
            used.add(item_id)  # Deleted rows must not be added back by autofill.
            if data.get("DELETE"):
                continue
            item = available.get(item_id)
            if item is None:
                form.add_error("order_item", _("Строка не соответствует заказу и организации поступления."))
            elif data["quantity"] > item.remaining_quantity:
                form.add_error("quantity", _("Доступно к поступлению: %(quantity)s.") % {"quantity": item.remaining_quantity})
        if any(self.errors):
            return
        if self.data.get("fill_from_order"):
            for item in available.values():
                if item.pk in used or item.remaining_quantity <= 0:
                    continue
                row = GoodsReceiptItem(
                    receipt=self.instance, order_item=item,
                    quantity=item.remaining_quantity,
                    sort_order=len(self.forms) + len(self.pending_items),
                )
                try:
                    row.full_clean(exclude=("receipt",))
                except forms.ValidationError as error:
                    raise forms.ValidationError(
                        _("Не удалось заполнить товар %(product)s: %(error)s"),
                        params={"product": item.product, "error": "; ".join(error.messages)},
                    ) from error
                self.pending_items.append(row)
        active = [f for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]
        if self.instance.is_applied and not active and not self.pending_items:
            raise forms.ValidationError(_("Нельзя провести поступление без позиций."))

    def save_new_objects(self, commit=True):
        objects = super().save_new_objects(commit=commit)
        for item in self.pending_items:
            item.receipt = self.instance
            if commit:
                item.save()
            objects.append(item)
        return objects


class GoodsReceiptItemInline(TabularInline):
    model = GoodsReceiptItem
    form = GoodsReceiptItemForm
    formset = GoodsReceiptItemFormSet
    fields = ("sort_order", "order_item", "quantity", "line_total")
    readonly_fields = ("line_total",)
    extra = 0

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("order_item")

    @admin.display(description=_("Сумма по цене заказа"))
    def line_total(self, obj):
        return obj.total_price if obj and obj.pk else "—"



@admin.register(GoodsReceipt)
class GoodsReceiptAdmin(SourceOrderAdminMixin, OrderTotalsAdminMixin, BaseDocumentAdmin):
    form = GoodsReceiptForm
    total_field = F("items__quantity") * F("items__order_item__purchase_price")
    product_field = "items__order_item__product"
    list_display = ("id", "supplier_delivery_note_number", "supplier_delivery_note_date", "supplier", "organization", "warehouse", "order_total", "is_applied", "created")
    list_display_links = ("id", "supplier")
    search_fields = ("supplier_delivery_note_number",)
    list_filter = ("is_applied", "organization", "warehouse", "supplier")
    fields = BASE_FIELDS + (
        ("supplier_delivery_note_number", "supplier_delivery_note_date"),
        "supplier", "orders", "warehouse", "fill_from_order",
        ("order_total", "order_quantity", "product_count"), "comment",
    )
    readonly_fields = BASE_READONLY + ( "order_total", "order_quantity", "product_count")
    inlines = (GoodsReceiptItemInline,)

    filter_horizontal = ("orders",)

    class Media:
        js = [
            "https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js",
            "documents/js/admin_sortable_init.js?v=2",
            "documents/js/admin_quantity_step.js",
        ]
