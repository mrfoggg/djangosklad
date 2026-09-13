from decimal import Decimal

from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.forms.models import BaseInlineFormSet
from django.utils.translation import gettext_lazy as _
from unfold.admin import TabularInline
from unfold.widgets import UnfoldAdminSelectWidget

from .admin import BaseDocumentAdmin, DocumentForm, BASE_FIELDS, SourceOrderFormSetMixin
from .models import SalesDocument, Shipment, ShipmentItem
from .shipments import shipment_sales_items


class DeliveryMethodWidget(UnfoldAdminSelectWidget):
    def create_option(self, *args, **kwargs):
        option = super().create_option(*args, **kwargs)
        method = getattr(option["value"], "instance", None)
        if method:
            option["attrs"]["data-kind"] = method.kind
        return option


class ShipmentForm(DocumentForm):
    fill_remaining = forms.BooleanField(
        label=_("Заполнить остатками реализации"), required=False,
        help_text=_("Добавит отсутствующие строки. Проведённые отгрузки уменьшают остаток."),
    )

    class Meta:
        model = Shipment
        fields = "__all__"
        widgets = {"delivery_method": DeliveryMethodWidget()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sales_document"].queryset = SalesDocument.objects.filter(is_applied=True, to_remove=False)


class ShipmentSalesItemWidget(UnfoldAdminSelectWidget):
    def create_option(self, *args, **kwargs):
        option = super().create_option(*args, **kwargs)
        item = getattr(option["value"], "instance", None)
        if item:
            unit = item.order_item.product.unit
            option["attrs"]["data-quantity-decimal-places"] = unit.decimal_places
            option["attrs"]["data-unit-symbol"] = unit.symbol
        return option


class ShipmentItemForm(forms.ModelForm):
    class Meta:
        model = ShipmentItem
        fields = "__all__"
        widgets = {"sales_item": ShipmentSalesItemWidget()}
        labels = {"sort_order": "⇅"}

    def __init__(self, *args, shipment_context=None, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields["sales_item"]
        if shipment_context:
            field.queryset = shipment_sales_items(shipment_context).filter(
                Q(remaining_quantity__gt=0) | Q(pk=self.instance.sales_item_id),
            )
        else:
            field.queryset = field.queryset.none()
        field.label_from_instance = self.label
        if self.instance.pk:
            field.disabled = True
            places = self.instance.sales_item.order_item.product.unit.decimal_places
            self.fields["quantity"].widget.attrs["step"] = f"{Decimal(1).scaleb(-places):f}"

    @staticmethod
    def label(item):
        unit = item.order_item.product.unit.symbol
        return (
            f"Реализация №{item.document_id} | {item.order_item.product} | "
            f"{item.quantity.normalize():f} {unit} · Осталось отгрузить: {item.displayed_remaining.normalize():f} {unit}"
        )


class ShipmentItemFormSet(SourceOrderFormSetMixin, BaseInlineFormSet):
    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        context = Shipment(pk=self.instance.pk, organization_id=None)
        for field in ("organization", "sales_document"):
            value = self.data.get(field) if self.is_bound else getattr(self.instance, f"{field}_id")
            try:
                value = int(value) if value else None
            except (ValueError, TypeError):
                value = None
            setattr(context, f"{field}_id", value)
        kwargs["shipment_context"] = context
        return kwargs

    def clean(self):
        super().clean()
        self.pending_items = []
        if any(self.errors) or not self.instance.sales_document_id:
            return
        list(self.instance.sales_document.items.select_for_update().order_by("pk").values_list("pk", flat=True))
        available = {item.pk: item for item in shipment_sales_items(self.instance)}
        used = set()
        active = []
        for form in self.forms:
            data = form.cleaned_data
            if not data or not data.get("sales_item"):
                continue
            pk = data["sales_item"].pk
            used.add(pk)
            if data.get("DELETE"):
                continue
            active.append(form)
            item = available.get(pk)
            if not item:
                form.add_error("sales_item", _("Строка не соответствует реализации и организации отгрузки."))
            elif data["quantity"] > item.remaining_quantity:
                form.add_error("quantity", _("Доступно к отгрузке: %(quantity)s.") % {"quantity": item.remaining_quantity})
        if any(self.errors):
            return
        if self.data.get("fill_remaining"):
            for item in available.values():
                if item.pk in used or item.remaining_quantity <= 0:
                    continue
                row = ShipmentItem(shipment=self.instance, sales_item=item, quantity=item.remaining_quantity,
                                   sort_order=len(self.forms) + len(self.pending_items))
                row.full_clean(exclude=("shipment",))
                self.pending_items.append(row)
        if self.instance.is_applied and not active and not self.pending_items:
            raise forms.ValidationError(_("Нельзя провести отгрузку без позиций."))

    def save_new_objects(self, commit=True):
        rows = super().save_new_objects(commit=commit)
        for item in self.pending_items:
            item.shipment = self.instance
            if commit:
                item.save()
            rows.append(item)
        return rows


class ShipmentItemInline(TabularInline):
    model = ShipmentItem
    form = ShipmentItemForm
    formset = ShipmentItemFormSet
    fields = ("sort_order", "sales_item", "quantity")
    extra = 0


@admin.register(Shipment)
class ShipmentAdmin(BaseDocumentAdmin):
    form = ShipmentForm
    fields = BASE_FIELDS + (
        "sales_document", "delivery_method", "status", "shipment_date",
        "recipient_name", "recipient_phone", "destination", "city", "branch", "address",
        "tracking_number", "pickup_location", "received_by", "handover_confirmed",
        "fill_remaining", "comment",
    )
    list_display = ("id", "sales_document", "delivery_method", "status", "shipment_date", "tracking_number", "is_applied")
    list_filter = ("delivery_method", "status", "is_applied", "organization")
    search_fields = ("tracking_number", "recipient_name", "recipient_phone")
    inlines = (ShipmentItemInline,)

    carrier = "delivery_method && document.getElementById('id_delivery_method').selectedOptions[0]?.dataset.kind === 'carrier'"
    pickup = "delivery_method && document.getElementById('id_delivery_method').selectedOptions[0]?.dataset.kind === 'pickup'"
    conditional_fields = {
        **BaseDocumentAdmin.conditional_fields,
        "destination": carrier,
        "city": carrier,
        "tracking_number": carrier,
        "branch": f"({carrier}) && destination === 'branch'",
        "address": f"({carrier}) && destination === 'address'",
        "pickup_location": pickup,
        "received_by": pickup,
        "handover_confirmed": pickup,
        "fill_remaining": "is_applied == false",
    }

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        value = request.GET.get("sales_document")
        if value:
            try:
                sale = SalesDocument.objects.get(pk=value, is_applied=True, to_remove=False)
            except (SalesDocument.DoesNotExist, ValueError, TypeError):
                raise PermissionDenied
            if not self.admin_site._registry[SalesDocument].has_view_permission(request, sale):
                raise PermissionDenied
            initial.update(organization=sale.organization_id, recipient_name=str(sale.customer), is_applied=False)
        return initial

    def get_formset_kwargs(self, request, obj, inline, prefix):
        kwargs = super().get_formset_kwargs(request, obj, inline, prefix)
        if request.method == "GET" and not obj.pk and request.GET.get("sales_document"):
            initial = self.get_changeform_initial_data(request)
            obj.sales_document_id = int(initial["sales_document"])
            obj.organization_id = initial["organization"]
            kwargs["preview_rows"] = [
                {"sales_item": item.pk, "quantity": item.remaining_quantity, "sort_order": index}
                for index, item in enumerate(shipment_sales_items(obj).filter(remaining_quantity__gt=0))
            ]
        return kwargs

    class Media:
        js = [
            "https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js",
            "documents/js/admin_sortable_init.js?v=3",
            "documents/js/admin_quantity_step.js?v=2",
        ]
