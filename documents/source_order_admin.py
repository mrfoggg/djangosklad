"""Open unsaved invoice/receipt forms from a saved supplier order."""
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from unfold.forms import BaseDialogForm
from unfold.widgets import UnfoldAdminSelectWidget

from catalogs.models import Organization
from .invoices import invoice_order_items
from .models import GoodsReceipt, PurchaseInvoice, PurchaseOrder, CustomerOrder, SalesInvoice, SalesDocument
from .sales import sales_order_items
from .sales_invoices import sales_invoice_order_items
from .receipts import receipt_order_items


def source_order_organizations(order):
    row_ids = list(order.items.values_list("organization_id", flat=True))
    organization_ids = {value or order.organization_id for value in row_ids}
    if not row_ids:
        organization_ids.add(order.organization_id)
    organization_ids.discard(None)
    return Organization.objects.filter(pk__in=organization_ids).order_by("pk")


class SourceOrderOrganizationForm(BaseDialogForm):
    organization = forms.ModelChoiceField(
        queryset=Organization.objects.none(), label=_("Организация"),
        widget=UnfoldAdminSelectWidget,
    )

    def __init__(self, request, order, **kwargs):
        super().__init__(request, object_id=order.pk, **kwargs)
        self.fields["organization"].queryset = source_order_organizations(order)


class SourceOrderFormSetMixin:
    def __init__(self, *args, preview_rows=None, **kwargs):
        if preview_rows is not None:
            self.extra = len(preview_rows)
            kwargs["initial"] = preview_rows
        super().__init__(*args, **kwargs)


class SourceOrderAdminMixin:
    source_order_model = PurchaseOrder
    source_party_field = "supplier"

    def _source_order(self, request):
        if not request.GET.get("from_order"):
            return None
        if hasattr(request, "_source_order"):
            return request._source_order
        try:
            order = self.source_order_model.objects.get(pk=request.GET["from_order"])
        except (self.source_order_model.DoesNotExist, ValueError, TypeError):
            raise Http404
        order_admin = self.admin_site._registry[self.source_order_model]
        if not order_admin.has_view_permission(request, order):
            raise PermissionDenied
        if not order.is_applied or order.to_remove:
            raise PermissionDenied(_("Создание на основании доступно только для проведённого заказа."))
        request._source_order = order
        return order

    def _source_organizations(self, order):
        return source_order_organizations(order)

    def _source_organization_id(self, request, order):
        choices = self._source_organizations(order)
        value = request.GET.get("organization")
        if value:
            try:
                return choices.get(pk=value).pk
            except (Organization.DoesNotExist, ValueError, TypeError):
                raise PermissionDenied(_("Организация не соответствует заказу."))
        if choices.count() == 1:
            return choices.first().pk
        return None

    def add_view(self, request, form_url="", extra_context=None):
        order = self._source_order(request)
        if order and not self.has_add_permission(request):
            raise PermissionDenied
        if order and request.method == "GET" and not self._source_organization_id(request, order):
            choices = self._source_organizations(order)
            if not choices.exists():
                self.message_user(request, _("Сначала укажите организацию в заказе или его строках."), messages.WARNING)
                return HttpResponseRedirect(reverse(f"admin:documents_{self.source_order_model._meta.model_name}_change", args=[order.pk]))
            class OrganizationForm(forms.Form):
                organization = forms.ModelChoiceField(
                    queryset=choices, label=_("Организация"), widget=UnfoldAdminSelectWidget,
                )
            return TemplateResponse(request, "admin/documents/choose_source_organization.html", {
                **self.admin_site.each_context(request), "title": _("Выберите организацию"),
                "opts": self.model._meta, "source_order": order, "form": OrganizationForm(),
            })
        return super().add_view(request, form_url, extra_context)

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        order = self._source_order(request)
        if order:
            party = self.source_party_field
            initial.update({party: getattr(order, f"{party}_id"),
                            "organization": self._source_organization_id(request, order),
                            "orders": [order.pk], "is_applied": False})
            context = self.model(**{f"{party}_id": initial[party], "organization_id": initial["organization"]})
            if self.model is GoodsReceipt:
                warehouses = list(receipt_order_items(context, [order.pk]).filter(
                    remaining_quantity__gt=0,
                ).values_list("warehouse_id", flat=True).order_by().distinct())
                if len(warehouses) == 1:
                    initial["warehouse"] = warehouses[0]
        return initial

    def get_formset_kwargs(self, request, obj, inline, prefix):
        kwargs = super().get_formset_kwargs(request, obj, inline, prefix)
        order = self._source_order(request)
        if order and request.method == "GET" and not obj.pk:
            setattr(obj, f"{self.source_party_field}_id", getattr(order, f"{self.source_party_field}_id"))
            obj.organization_id = self._source_organization_id(request, order)
            obj._selected_order_ids = [order.pk]
            if self.model in (PurchaseInvoice, SalesInvoice):
                fetch_items = invoice_order_items if self.model is PurchaseInvoice else sales_invoice_order_items
                candidates = fetch_items(obj, [order.pk]).filter(invoice_remaining__gt=0)
                quantity_field = "invoice_remaining"
            else:
                fetch_items = receipt_order_items if self.model is GoodsReceipt else sales_order_items
                candidates = fetch_items(obj, [order.pk]).filter(remaining_quantity__gt=0)
                quantity_field = "remaining_quantity"
            kwargs["preview_rows"] = [
                {"order_item": item.pk, "quantity": getattr(item, quantity_field), "sort_order": index}
                for index, item in enumerate(candidates)
            ]
            if not kwargs["preview_rows"]:
                self.message_user(request, _("По этому заказу нет доступного остатка для выбранного документа."), messages.WARNING)
        return kwargs
