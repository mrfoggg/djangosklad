import re

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.views.main import ChangeList
from django.http import HttpResponse, Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.db import connection, models
from django.db.models.functions import Collate
from django.utils.translation import gettext_lazy as _
from django.utils.html import format_html_join
from django_countries.widgets import CountrySelectWidget
from mptt.admin import DraggableMPTTAdmin
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.contrib.forms.widgets import WysiwygWidget
from unfold.decorators import action
from unfold.widgets import UnfoldAdminCheckboxSelectMultipleWidget

from .forms import NovaPoshtaRegionUpdateForm, NovaPoshtaSettlementUpdateForm, PhoneNumberForm
from .nova_poshta import sync_settlement_details, sync_warehouses, sync_warehouse_types
from .nova_poshta import NovaPoshtaError, sync_areas, sync_regions, sync_settlements, sync_settlement_types

from .models import (
    ContactPerson,
    ContactRole,
    ContractorContactPerson,
    ContractorPhone,
    ContactPersonPhone,
    PhoneNumber,
    Brand,
    BrandSupplier,
    Category,
    Contractor,
    ContractorBankAccount,
    ContractorLegalDetails,
    ContractorLink,
    MeasurementUnit,
    NovaPoshtaWarehouse,
    NovaPoshtaWarehouseType,
    NovaPoshtaArea,
    NovaPoshtaRegion,
    NovaPoshtaSettlement,
    NovaPoshtaSettlementDetails,
    NovaPoshtaSettlementType,
    Organization,
    OurBankAccount,
    Product,
    ProductSupplier,
    RetailStore,
    Settlement,
    SettlementType,
    Warehouse,
)

BASE_READONLY_DATES = ("created", "updated")


class NovaPoshtaChangeList(ChangeList):
    def get_ordering(self, request, queryset):
        ordering = super().get_ordering(request, queryset)
        if connection.vendor != "sqlite":
            return ordering
        result = []
        for field in ordering:
            if isinstance(field, str):
                name = field.lstrip("-")
                name = {
                    "area": "area__description", "region": "region__description",
                    "settlement_type": "settlement_type__description",
                }.get(name, name)
                if name in ("description", "area__description", "region__description", "settlement_type__description"):
                    expression = Collate(name, "ukrainian")
                    result.append(expression.desc() if field.startswith("-") else expression.asc())
                    continue
            result.append(field)
        return result


class NovaPoshtaCatalogAdmin(ModelAdmin):
    ordering = ("description", "ref")

    def get_changelist(self, request, **kwargs):
        return NovaPoshtaChangeList

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def get_list_display(self, request):
        return (*super().get_list_display(request), "is_active", "created", "updated")

    def get_list_filter(self, request):
        return (*super().get_list_filter(request), "is_active")

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if self.fieldsets:
            return (*fieldsets, (_("Даты"), {"fields": ("is_active", "created", "updated")}))
        return fieldsets

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_sync_permission(self, request):
        opts = self.model._meta
        return request.user.has_perms([
            f"{opts.app_label}.add_{opts.model_name}",
            f"{opts.app_label}.change_{opts.model_name}",
        ])


class NovaPoshtaReadonlyInline(TabularInline):
    extra = 0
    can_delete = False
    show_change_link = True
    per_page = 20
    ordering = ("description", "ref")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if connection.vendor == "sqlite":
            return queryset.order_by(Collate("description", "ukrainian"), "ref")
        return queryset

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class NovaPoshtaRegionInline(NovaPoshtaReadonlyInline):
    tab = True
    verbose_name_plural = _("Районы")
    model = NovaPoshtaRegion
    fk_name = "area"
    fields = ("description", "region_type", "is_active", "created", "updated")
    readonly_fields = fields


class NovaPoshtaSettlementInline(NovaPoshtaReadonlyInline):
    model = NovaPoshtaSettlement
    fk_name = "region"
    fields = ("description", "settlement_type", "warehouse", "address_delivery_allowed", "is_active", "updated")
    readonly_fields = fields

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("settlement_type")


class NovaPoshtaAreaSettlementInline(NovaPoshtaSettlementInline):
    fk_name = "area"
    tab = True
    verbose_name_plural = _("Населённые пункты")
    fields = ("description", "settlement_type", "region", "warehouse", "is_active", "updated")
    readonly_fields = fields

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("region")


@admin.register(NovaPoshtaArea)
class NovaPoshtaAreaAdmin(NovaPoshtaCatalogAdmin):
    list_display = ("description", "ref")
    search_fields = ("description", "ref")
    actions_list = ("update_areas",)
    inlines = (NovaPoshtaRegionInline, NovaPoshtaAreaSettlementInline)

    def get_search_results(self, request, queryset, search_term):
        results, may_have_duplicates = super().get_search_results(
            request, queryset, search_term
        )
        if search_term:
            # SQLite LIKE не поддерживает регистронезависимый поиск кириллицы.
            # Справочник областей мал, поэтому сравниваем названия через casefold.
            term = search_term.strip().casefold()
            matching_refs = [
                ref for ref, description in queryset.values_list("ref", "description")
                if term in description.casefold()
            ]
            results = results | queryset.filter(ref__in=matching_refs)
        return results, may_have_duplicates

    @action(
        description=_("Обновить области"),
        icon="sync",
        url_path="update-areas",
        permissions=["sync"],
        dialog={
            "title": _("Обновить области Новой почты"),
            "description": _("Загрузить актуальные названия и добавить новые области."),
            "form_submit_text": _("Обновить"),
        },
    )
    def update_areas(self, request, form):
        try:
            result = sync_areas()
        except NovaPoshtaError as exc:
            self.message_user(request, str(exc), messages.ERROR)
        else:
            self.message_user(
                request,
                _("Области обновлены. Добавлено: %(created)s, изменено: %(updated)s, без изменений: %(unchanged)s, отключено: %(deactivated)s.")
                % vars(result),
                messages.SUCCESS,
            )
        url = reverse("admin:catalogs_novaposhtaarea_changelist")
        if request.headers.get("HX-Request") == "true":
            return HttpResponse(headers={"HX-Redirect": url})
        return redirect(url)


@admin.register(NovaPoshtaRegion)
class NovaPoshtaRegionAdmin(NovaPoshtaCatalogAdmin):
    list_display = ("description", "area", "region_type", "ref")
    list_filter = ("area",)
    list_select_related = ("area",)
    search_fields = ("description", "ref", "area__description")
    autocomplete_fields = ("area",)
    actions_list = ("update_regions",)
    inlines = (NovaPoshtaSettlementInline,)

    @action(
        description=_("Обновить районы"),
        icon="sync",
        url_path="update-regions",
        permissions=["sync"],
        dialog={
            "title": _("Обновить районы Новой почты"),
            "form_class": NovaPoshtaRegionUpdateForm,
            "form_submit_text": _("Обновить"),
        },
    )
    def update_regions(self, request, form):
        try:
            result = sync_regions(area=form.cleaned_data["area"])
        except NovaPoshtaError as exc:
            self.message_user(request, str(exc), messages.ERROR)
        else:
            self.message_user(
                request,
                _("Районы обновлены. Добавлено: %(created)s, изменено: %(updated)s, без изменений: %(unchanged)s, отключено: %(deactivated)s.")
                % vars(result),
                messages.SUCCESS,
            )
        url = reverse("admin:catalogs_novaposhtaregion_changelist")
        if request.headers.get("HX-Request") == "true":
            return HttpResponse(headers={"HX-Redirect": url})
        return redirect(url)


@admin.register(NovaPoshtaSettlementType)
class NovaPoshtaSettlementTypeAdmin(NovaPoshtaCatalogAdmin):
    list_display = ("description", "code", "ref")
    search_fields = ("description", "code", "ref")
    actions_list = ("update_settlement_types",)

    @action(
        description=_("Обновить типы населённых пунктов"),
        icon="sync", url_path="update-settlement-types", permissions=["sync"],
        dialog={
            "title": _("Обновить типы населённых пунктов"),
            "form_submit_text": _("Обновить"),
        },
    )
    def update_settlement_types(self, request, form):
        try:
            result = sync_settlement_types()
        except NovaPoshtaError as exc:
            self.message_user(request, str(exc), messages.ERROR)
        else:
            self.message_user(
                request,
                _("Типы обновлены. Добавлено: %(created)s, изменено: %(updated)s, без изменений: %(unchanged)s, отключено: %(deactivated)s.") % vars(result),
                messages.SUCCESS,
            )
        url = reverse("admin:catalogs_novaposhtasettlementtype_changelist")
        if request.headers.get("HX-Request") == "true":
            return HttpResponse(headers={"HX-Redirect": url})
        return redirect(url)


class NovaPoshtaSettlementDetailsInline(StackedInline):
    model = NovaPoshtaSettlementDetails
    extra = 0
    can_delete = False
    readonly_fields = tuple(
        field.name for field in NovaPoshtaSettlementDetails._meta.fields
        if field.name != "settlement"
    )
    fields = readonly_fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("catalogs.view_novaposhtasettlement") or request.user.has_perm("catalogs.change_novaposhtasettlement")


class NovaPoshtaWarehouseInline(TabularInline):
    model = NovaPoshtaWarehouse
    extra = 0
    can_delete = False
    show_change_link = True
    per_page = 20
    tab = True
    fields = ("number", "description", "warehouse_type", "category", "status", "total_max_weight", "is_active")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(NovaPoshtaWarehouseType)
class NovaPoshtaWarehouseTypeAdmin(NovaPoshtaCatalogAdmin):
    list_display = ("description", "description_ru", "ref")
    search_fields = ("description", "description_ru", "ref")
    actions_list = ("update_types",)

    @action(
        description=_("Обновить типы отделений"), icon="sync", url_path="update-types",
        permissions=["sync"],
        dialog={"title": _("Обновить типы отделений Новой почты"), "form_submit_text": _("Обновить")},
    )
    def update_types(self, request, form):
        try:
            result = sync_warehouse_types()
        except NovaPoshtaError as exc:
            self.message_user(request, str(exc), messages.ERROR)
        else:
            self.message_user(request, _("Добавлено: %(created)s, изменено: %(updated)s, без изменений: %(unchanged)s, отключено: %(deactivated)s.") % vars(result), messages.SUCCESS)
        url = reverse("admin:catalogs_novaposhtawarehousetype_changelist")
        if request.headers.get("HX-Request") == "true":
            return HttpResponse(headers={"HX-Redirect": url})
        return redirect(url)


@admin.register(NovaPoshtaWarehouse)
class NovaPoshtaWarehouseAdmin(NovaPoshtaCatalogAdmin):
    list_display = ("number", "description", "settlement", "warehouse_type", "category", "status")
    search_fields = ("description", "description_ru", "number", "short_address", "settlement_description", "site_key", "ref")
    list_filter = ("warehouse_type", "category", "status")
    list_select_related = ("settlement", "warehouse_type")
    actions_list = ("update_warehouses",)

    fieldsets = (
        (_("Отделение"), {"fields": ('number', 'site_key', 'description', 'description_ru', 'warehouse_type', 'category', 'status', 'status_date', 'phone', 'warehouse_index')}),
        (_("Адрес"), {"fields": ('settlement', 'short_address', 'short_address_ru', 'postal_code', 'latitude', 'longitude', 'city_description', 'city_description_ru', 'settlement_description', 'settlement_area_description', 'settlement_regions_description', 'settlement_type_description', 'settlement_type_description_ru', 'region_city', 'location')}),
        (_("Услуги и оборудование"), {"fields": ('post_finance', 'bicycle_parking', 'payment_access', 'pos_terminal', 'international_shipping', 'warehouse_illusha', 'warehouse_for_agent', 'generator_enabled', 'work_in_mobile_awis', 'deny_to_select', 'can_get_money_transfer', 'has_mirror', 'has_fitting_room', 'only_receiving_parcel')}),
        (_("Ограничения и почтоматы"), {"fields": ('total_max_weight', 'place_max_weight', 'max_declared_cost', 'self_service_workplaces_count', 'sending_dimensions', 'receiving_dimensions', 'post_machine_type', 'postomat_for')}),
        (_("Графики"), {"fields": ('schedule', 'reception', 'delivery')}),
        (_("Идентификаторы источника"), {"fields": ('ref', 'settlement_ref', 'city_ref', 'district_code', 'direct', 'beacon_code')}),
        (_("Исходный ответ API"), {"fields": ("raw_data",), "classes": ("collapse",)}),
    )

    @action(
        description=_("Обновить все отделения"), icon="sync", url_path="update-warehouses",
        permissions=["sync"],
        dialog={"title": _("Обновить отделения и почтоматы Новой почты"),
                "description": _("Загрузить все страницы справочника. Это может занять несколько минут."),
                "form_submit_text": _("Обновить")},
    )
    def update_warehouses(self, request, form):
        try:
            result = sync_warehouses()
        except NovaPoshtaError as exc:
            self.message_user(request, str(exc), messages.ERROR)
        else:
            self.message_user(request, _("Сохранено: %(processed)s, отключено: %(deactivated)s.") % vars(result), messages.SUCCESS)
        url = reverse("admin:catalogs_novaposhtawarehouse_changelist")
        if request.headers.get("HX-Request") == "true":
            return HttpResponse(headers={"HX-Redirect": url})
        return redirect(url)


@admin.register(NovaPoshtaSettlement)
class NovaPoshtaSettlementAdmin(NovaPoshtaCatalogAdmin):
    list_display = ("description", "settlement_type", "area", "region", "warehouse", "address_delivery_allowed")
    list_filter = ("area", "settlement_type", "warehouse", "address_delivery_allowed")
    list_select_related = ("area", "region", "settlement_type")
    search_fields = ("description", "description_ru", "description_translit", "ref")
    autocomplete_fields = ("area", "region")
    actions_list = ("update_settlements",)
    actions_detail = ("update_details", "update_warehouses")
    inlines = (NovaPoshtaSettlementDetailsInline, NovaPoshtaWarehouseInline)

    def has_update_warehouses_permission(self, request, object_id=None):
        return request.user.has_perms(("catalogs.add_novaposhtawarehouse", "catalogs.change_novaposhtawarehouse"))

    @action(
        description=_("Обновить отделения"), icon="sync", url_path="update-warehouses",
        permissions=["update_warehouses"],
        dialog={"title": _("Обновить отделения населённого пункта"), "form_submit_text": _("Обновить")},
    )
    def update_warehouses(self, request, form, object_id):
        settlement = self.get_object(request, object_id)
        if settlement is None:
            raise Http404
        try:
            result = sync_warehouses(settlement)
        except NovaPoshtaError as exc:
            self.message_user(request, str(exc), messages.ERROR)
        else:
            self.message_user(request, _("Сохранено: %(processed)s, отключено: %(deactivated)s.") % vars(result), messages.SUCCESS)
        url = reverse("admin:catalogs_novaposhtasettlement_change", args=[settlement.pk])
        if request.headers.get("HX-Request") == "true":
            return HttpResponse(headers={"HX-Redirect": url})
        return redirect(url)

    def has_update_details_permission(self, request, object_id=None):
        return request.user.has_perm("catalogs.change_novaposhtasettlement")

    @action(
        description=_("Обновить допданные"), icon="sync",
        url_path="update-details", permissions=["update_details"],
        dialog={
            "title": _("Обновить допданные населённого пункта"),
            "description": _("Получить сведения о доставке, доступности справочника улиц и городе доставки из Новой почты."),
            "form_submit_text": _("Обновить"),
        },
    )
    def update_details(self, request, form, object_id):
        settlement = self.get_object(request, object_id)
        if settlement is None:
            raise Http404
        try:
            sync_settlement_details(settlement)
        except NovaPoshtaError as exc:
            self.message_user(request, str(exc), messages.ERROR)
        else:
            self.message_user(request, _("Допданные обновлены."), messages.SUCCESS)
        url = reverse("admin:catalogs_novaposhtasettlement_change", args=[settlement.pk])
        if request.headers.get("HX-Request") == "true":
            return HttpResponse(headers={"HX-Redirect": url})
        return redirect(url)
    fieldsets = (
        (_("Населённый пункт"), {"fields": (
            "ref", ("area", "region"), "description", "description_ru", "description_translit",
        )}),
        (_("Тип"), {"fields": (
            "settlement_type", "settlement_type_description",
            "settlement_type_description_ru", "settlement_type_description_translit",
        )}),
        (_("Координаты и индексы"), {"fields": (
            ("latitude", "longitude"), ("index_1", "index_2"), "index_coatsu_1",
        )}),
        (_("Доставка"), {"fields": (
            ("warehouse", "address_delivery_allowed"),
            ("delivery_1", "delivery_2", "delivery_3", "delivery_4"),
            ("delivery_5", "delivery_6", "delivery_7"),
            "special_cash_check", "radius_home_delivery", "radius_express_pick_up", "radius_drop",
        )}),
    )

    def get_search_results(self, request, queryset, search_term):
        if connection.vendor == "sqlite" and search_term:
            # SQLite REGEXP в Django использует Python re с поддержкой Unicode.
            query = models.Q()
            for field in self.search_fields:
                if field != "ref":
                    query |= models.Q(**{f"{field}__iregex": re.escape(search_term.strip())})
            try:
                from uuid import UUID
                query |= models.Q(ref=UUID(search_term))
            except ValueError:
                pass
            return queryset.filter(query), False
        return super().get_search_results(request, queryset, search_term)

    @action(
        description=_("Обновить населённые пункты"),
        icon="sync",
        url_path="update-settlements",
        permissions=["sync"],
        dialog={
            "title": _("Обновить населённые пункты Новой почты"),
            "form_class": NovaPoshtaSettlementUpdateForm,
            "form_submit_text": _("Обновить"),
        },
    )
    def update_settlements(self, request, form):
        try:
            result = sync_settlements(area=form.cleaned_data["area"])
        except NovaPoshtaError as exc:
            self.message_user(request, str(exc), messages.ERROR)
        else:
            self.message_user(
                request,
                _("Населённые пункты обновлены. Загружено и сохранено: %(processed)s, отключено: %(deactivated)s.")
                % vars(result),
                messages.SUCCESS,
            )
        url = reverse("admin:catalogs_novaposhtasettlement_changelist")
        if request.headers.get("HX-Request") == "true":
            return HttpResponse(headers={"HX-Redirect": url})
        return redirect(url)


class BaseCatalogAdmin(ModelAdmin):
    readonly_fields = BASE_READONLY_DATES
    formfield_overrides = {
        models.TextField: {
            "widget": WysiwygWidget,
        }
    }


# --- ИНЛАЙНЫ (Вспомогательные модели внутри основных) ---


class ContractorBankAccountInline(TabularInline):
    model = ContractorBankAccount
    extra = 1
    # Для удобства в Unfold можно использовать компактное отображение
    fields = ("bank_name", "iban", "currency")
    tab = True


class OurBankAccountInline(TabularInline):
    model = OurBankAccount
    extra = 1
    fields = ("bank_name", "mfo", "iban", "currency", "is_default", "note")
    tab = True


class BrandSupplierInline(TabularInline):
    model = BrandSupplier
    extra = 1
    autocomplete_fields = ["supplier"]


class ContractorLinkInline(TabularInline):
    model = ContractorLink
    extra = 1  # Одна пустая строка для новой ссылки
    fields = ("name", "url")
    tab = True


class LegalDetailsInline(StackedInline):
    model = ContractorLegalDetails
    can_delete = False
    verbose_name = _("Юридические реквизиты")
    verbose_name_plural = _("Юридические реквизиты")
    fields = ("inn", "legal_address")
    tab = True


class ProductSupplierInline(TabularInline):
    """Отображение поставщиков прямо в карточке товара"""

    model = ProductSupplier
    extra = 1
    # Ограничиваем выбор только теми, кто реально является поставщиком
    autocomplete_fields = ["supplier"]
    fields = ("supplier", "supplier_sku")
    verbose_name = _("Связь с поставщиком")
    verbose_name_plural = _("Список поставщиков этого товара")


class SubsidiariesInline(TabularInline):
    model = Contractor
    fk_name = "parent_holding"

    # Убираем пустые строки для создания
    extra = 0
    max_num = 0

    # Оставляем только нужные поля и делаем их только для чтения
    fields = ("last_name", "legal_type", "is_supplier", "is_customer")
    readonly_fields = ("last_name", "legal_type", "is_supplier", "is_customer")

    # Добавляем ссылку для быстрого перехода в карточку дочерней компании
    show_change_link = True

    verbose_name = _("Дочерняя компания")
    verbose_name_plural = _("Входящие в холдинг компании")


# --- АДМИН-КЛАССЫ ---


@admin.register(Category)
class CategoryAdmin(DraggableMPTTAdmin, ModelAdmin):
    # Настройки отображения дерева
    # mptt_level_indent — отступ каждой ветки в пикселях
    mptt_level_indent = 20

    # Поля, которые будут отображаться в списке
    # 'tree_actions' — это кнопки развернуть/свернуть и ручка для перетаскивания
    # 'indented_title' — название категории с учетом вложенности
    list_display = (
        "tree_actions",
        "indented_title",
        "slug",
        "created",
    )

    list_display_links = ("indented_title",)

    # Автоматическая генерация слага из названия
    prepopulated_fields = {"slug": ("name",)}

    # Поиск по категориям
    search_fields = ("name", "slug")

    # Настройки для Unfold (Tailwind стилизация)
    list_filter_submit = True  # Кнопка применения фильтров


class PhoneLinkInline(StackedInline):
    extra = 0
    tab = True
    autocomplete_fields = ("phone",)
    fields = ("phone", "label", ("for_communication", "is_primary_for_communication"), ("for_delivery", "is_primary_for_delivery"), "comment")


class ContractorPhoneInline(PhoneLinkInline):
    model = ContractorPhone


class ContactPersonPhoneInline(PhoneLinkInline):
    model = ContactPersonPhone


class ContractorContactPersonInline(TabularInline):
    model = ContractorContactPerson
    extra = 0
    tab = True
    autocomplete_fields = ("contact_person",)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == "roles":
            kwargs["widget"] = UnfoldAdminCheckboxSelectMultipleWidget
            kwargs["help_text"] = _("Можно выбрать несколько ролей.")
        return super().formfield_for_manytomany(db_field, request, **kwargs)


class ContactPersonContractorInline(ContractorContactPersonInline):
    autocomplete_fields = ("contractor",)


@admin.register(PhoneNumber)
class PhoneNumberAdmin(ModelAdmin):
    form = PhoneNumberForm
    list_display = ("number", "number_info", "has_viber", "has_telegram", "has_whatsapp")
    search_fields = ("number",)
    list_filter = ("has_viber", "has_telegram", "has_whatsapp")
    readonly_fields = (*BASE_READONLY_DATES, "number_info", "linked_contractors", "linked_contact_persons")
    fields = (
        "number",
        "number_info",
        ("has_viber", "has_telegram", "has_whatsapp"),
        "linked_contractors",
        "linked_contact_persons",
        ("created", "updated"),
    )


    @admin.display(description=_("Информация о номере"), empty_value="—")
    def number_info(self, obj):
        if not obj:
            return ""
        return " · ".join(
            str(value) for value in (
                obj.number_type_label, obj.operator_name, obj.region_description,
            ) if value
        )

    @admin.display(description=_("Связанные контрагенты"))
    def linked_contractors(self, obj):
        if not obj or not obj.pk:
            return "—"
        owners = Contractor.objects.filter(phones=obj)
        return format_html_join(
            ", ", '<a href="{}">{}</a>',
            ((reverse("admin:catalogs_contractor_change", args=[owner.pk]), str(owner)) for owner in owners),
        ) or "—"

    @admin.display(description=_("Связанные контактные лица"))
    def linked_contact_persons(self, obj):
        if not obj or not obj.pk:
            return "—"
        owners = ContactPerson.objects.filter(phones=obj)
        return format_html_join(
            ", ", '<a href="{}">{}</a>',
            ((reverse("admin:catalogs_contactperson_change", args=[owner.pk]), str(owner)) for owner in owners),
        ) or "—"


@admin.register(ContactRole)
class ContactRoleAdmin(ModelAdmin):
    list_display = ("name", "code")
    search_fields = ("name", "code")


@admin.register(ContactPerson)
class ContactPersonAdmin(ModelAdmin):
    list_display = ("last_name", "first_name", "middle_name", "email")
    search_fields = ("last_name", "first_name", "middle_name", "email", "phones__number")
    readonly_fields = BASE_READONLY_DATES
    exclude = ("phones",)
    inlines = (ContactPersonPhoneInline, ContactPersonContractorInline)


@admin.register(Contractor)
class ContractorAdmin(BaseCatalogAdmin):
    # Поиск по ИНН работает через связь legal_details
    search_fields = ("last_name", "first_name", "email", "legal_details__inn")

    list_display = (
        "get_full_name",
        "legal_type",
        "parent_holding",
        "is_supplier",
        "is_customer",
    )

    list_filter = (
        "legal_type",
        "is_supplier",
        "is_customer",
    )

    def get_inlines(self, request, obj=None):
        inlines = [
            ContractorPhoneInline,
            ContractorContactPersonInline,
            LegalDetailsInline,
            ContractorBankAccountInline,
            ContractorLinkInline,
        ]
        if obj and obj.legal_type == "HLD":
            inlines.append(SubsidiariesInline)
        if obj and obj.is_supplier:
            pass
        if obj and obj.is_customer:
            pass
        return inlines

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """
        Фильтруем список счетов, чтобы показать только те,
        которые принадлежат текущему контрагенту.
        """
        if db_field.name == "primary_account":
            # Извлекаем ID объекта из URL (в админке это обычно /change/1/)
            object_id = request.resolver_match.kwargs.get("object_id")

            if object_id:
                kwargs["queryset"] = ContractorBankAccount.objects.filter(
                    contractor_id=object_id
                )
            else:
                # Если это создание нового контрагента (объекта еще нет),
                # счетов быть не может
                kwargs["queryset"] = ContractorBankAccount.objects.none()

        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    fields = (
        "legal_type",
        "ownership_type",
        ("last_name", "first_name", "middle_name"),
        "parent_holding",
        ("is_supplier", "is_customer", "is_manufacturer"),
        ("use_usd_prices", "usd_rate", "default_price_type"),
        ("email", "primary_account"),
    )

    conditional_fields = {
        # Скрываем родителя для самих холдингов
        "parent_holding": "legal_type !== 'HLD'",
        # Показываем тип собственности (ООО, ЗАО) только для организаций
        "ownership_type": "legal_type === 'OTH'",
        # Поле "Цены в USD" показываем только поставщикам
        "use_usd_prices": "is_supplier === true",
        "is_manufacturer": "is_supplier === true",
        # А поле курса показываем только если это поставщик И он использует USD-прайсы
        "usd_rate": "is_supplier === true && use_usd_prices === true",
        "default_price_type": "is_supplier === true",
        "middle_name": "['IND', 'FOP'].includes(legal_type)",
        "first_name": "['IND', 'FOP'].includes(legal_type)",
    }

    @admin.display(description=_("Полное наименование"))
    def get_full_name(self, obj):
        return str(obj)

    get_full_name.admin_order_field = "last_name"


@admin.register(Organization)
class OrganizationAdmin(BaseCatalogAdmin):
    list_display = ("name", "inn", "is_default")
    list_editable = ("is_default",)
    inlines = [OurBankAccountInline]


@admin.register(MeasurementUnit)
class MeasurementUnitAdmin(BaseCatalogAdmin):
    readonly_fields = ()
    list_display = ("name", "symbol", "code", "decimal_places")
    search_fields = ("name", "symbol", "code")


@admin.register(Product)
class ProductAdmin(BaseCatalogAdmin):
    search_fields = ("name", "site_name", "sku", "external_id")

    # Подключаем возможность добавлять поставщиков в карточке товара
    inlines = [ProductSupplierInline]

    fieldsets = (
        (
            _("Наименования"),
            {
                "fields": ("name", "site_name", "fiscal_name"),
            },
        ),
        (
            _("Идентификаторы и атрибуты"),
            {
                "fields": (
                    ("sku", "external_id"),
                    ("brand", "main_supplier"),
                    "unit",
                    ("category", "subcategories"),
                ),
            },
        ),
    )

    @admin.display(description=_("Название"))
    def get_name(self, obj):
        return str(obj)

    get_name.admin_order_field = "name"

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Фильтр: в 'Основной поставщик' только те, кто уже добавлен к товару"""
        if db_field.name == "main_supplier":
            object_id = request.resolver_match.kwargs.get("object_id")
            if object_id:
                kwargs["queryset"] = ProductSupplier.objects.filter(
                    product_id=object_id
                )
            else:
                kwargs["queryset"] = ProductSupplier.objects.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(
        self, db_field, request, **kwargs
    ):  # Должно быть 3 аргумента кроме self
        if db_field.name == "subcategories":
            # Получаем текущий объект из контекста (если редактируем существующий)
            # В Django Admin объект обычно доступен через request или передается в метод
            # Но проще всего получить ID из URL, если мы на странице редактирования
            object_id = request.resolver_match.kwargs.get("object_id")

            if object_id:
                obj = self.get_object(request, object_id)
                if obj and obj.category:
                    # Фильтруем подкатегории (потомки выбранной категории)
                    kwargs["queryset"] = obj.category.get_descendants()
                else:
                    kwargs["queryset"] = Category.objects.none()
            else:
                # Если это создание нового товара
                kwargs["queryset"] = Category.objects.none()

        return super().formfield_for_manytomany(db_field, request, **kwargs)


@admin.register(ProductSupplier)
class ProductSupplierAdmin(BaseCatalogAdmin):
    """Отдельный список связей (если нужно править артикулы массово)"""

    list_display = ("product", "supplier", "supplier_sku")
    search_fields = ("product__name", "supplier__last_name", "supplier_sku")
    autocomplete_fields = ["product", "supplier"]


class BrandAdminForm(forms.ModelForm):
    class Meta:
        model = Brand
        fields = "__all__"
        widgets = {
            # Этот виджет как раз и рисует флаги в выпадающем списке
            "origin_country": CountrySelectWidget(),
            "production_country": CountrySelectWidget(),
        }


@admin.register(Brand)
class BrandAdmin(BaseCatalogAdmin):
    form = BrandAdminForm
    inlines = [BrandSupplierInline]
    search_fields = ("name",)

    # Исключаем оригинальное поле suppliers из формы,
    # так как мы управляем им через BrandSupplierInline
    exclude = ("suppliers",)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "main_supplier":
            object_id = request.resolver_match.kwargs.get("object_id")
            if object_id:
                # Показываем только тех, кто реально привязан к этому бренду
                kwargs["queryset"] = BrandSupplier.objects.filter(brand_id=object_id)
            else:
                kwargs["queryset"] = BrandSupplier.objects.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(BrandSupplier)
class BrandSupplierAdmin(BaseCatalogAdmin):
    """Отдельный список связей (если нужно править артикулы массово)"""

    list_display = ("brand", "supplier")
    search_fields = ("brand__name", "supplier__last_name")
    autocomplete_fields = ("supplier", "brand")


@admin.register(SettlementType)
class SettlementTypeAdmin(ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Settlement)
class SettlementAdmin(ModelAdmin):
    list_display = ("__str__", "country", "region")
    search_fields = ("name",)


@admin.register(Warehouse)
class WarehouseAdmin(BaseCatalogAdmin):
    # Используем названия полей именно из твоего последнего куска кода
    list_display = ["name", "settlement", "is_virtual"]
    list_filter = ["is_virtual", "settlement"]
    search_fields = ["name"]


@admin.register(RetailStore)
class RetailStoreAdmin(BaseCatalogAdmin):
    list_display = ("name", "url", "description", "is_default")
    list_editable = ("is_default",)
    search_fields = ("name", "description")


from .models import DeliveryMethod


@admin.register(DeliveryMethod)
class DeliveryMethodAdmin(BaseCatalogAdmin):
    list_display = ("name", "kind")
    list_filter = ("kind",)
    search_fields = ("name",)
