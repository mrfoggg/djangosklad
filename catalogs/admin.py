from django import forms
from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from django_countries.widgets import CountrySelectWidget
from unfold.admin import ModelAdmin, StackedInline, TabularInline

from .models import (
    Brand,
    BrandSupplier,
    Contractor,
    ContractorLegalDetails,
    ContractorLink,
    Product,
    ProductSupplier,
    Settlement,
    SettlementType,
    Warehouse,
)

# --- ИНЛАЙНЫ (Вспомогательные модели внутри основных) ---


class BrandSupplierInline(TabularInline):
    model = BrandSupplier
    extra = 1
    autocomplete_fields = ["supplier"]


class ContractorLinkInline(TabularInline):
    model = ContractorLink
    extra = 1  # Одна пустая строка для новой ссылки
    fields = ("name", "url")


class LegalDetailsInline(StackedInline):
    model = ContractorLegalDetails
    can_delete = False
    verbose_name = _("Юридические реквизиты")
    verbose_name_plural = _("Юридические реквизиты")
    fieldsets = ((None, {"fields": ("inn", "legal_address")}),)


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


@admin.register(Contractor)
class ContractorAdmin(ModelAdmin):
    # Поиск по ИНН работает через связь legal_details
    search_fields = ("last_name", "first_name", "email", "legal_details__inn")

    list_display = (
        "get_full_name",
        "legal_type",
        "parent_holding",
        "is_supplier",
        "is_customer",
        "dt_created",
    )

    list_filter = (
        "legal_type",
        "is_supplier",
        "is_customer",
        "dt_created",
    )

    inlines = (LegalDetailsInline, SubsidiariesInline, ContractorLinkInline)

    fieldsets = (
        (
            _("Основная информация"),
            {
                "fields": (
                    "legal_type",
                    "ownership_type",
                    "parent_holding",
                    ("last_name", "first_name", "middle_name"),
                )
            },
        ),
        (
            _("Настройки поставщика"),
            {
                "fields": (("use_usd_prices", "usd_rate"),),
                # Эта секция будет видна всегда, но поля внутри
                # скроются благодаря твоим conditional_fields
            },
        ),
        (
            _("Контакты и роли"),
            {"fields": (("email", "is_supplier", "is_customer"), "comment")},
        ),
        (
            _("Служебная информация"),
            {
                "fields": ("dt_created", "dt_updated"),
                "classes": ("collapse",),
            },
        ),
    )

    readonly_fields = ("dt_created", "dt_updated")

    conditional_fields = {
        # Скрываем родителя для самих холдингов
        "parent_holding": "legal_type !== 'HLD'",
        # Показываем тип собственности (ООО, ЗАО) только для организаций
        "ownership_type": "legal_type === 'OTH'",
        # Поле "Цены в USD" показываем только поставщикам
        "use_usd_prices": "is_supplier === true",
        # А поле курса показываем только если это поставщик И он использует USD-прайсы
        "usd_rate": "is_supplier === true && use_usd_prices === true",
        "middle_name": "['IND', 'FOP'].includes(legal_type)",
        "first_name": "['IND', 'FOP'].includes(legal_type)",
    }

    @admin.display(description=_("Полное наименование"))
    def get_full_name(self, obj):
        return str(obj)

    get_full_name.admin_order_field = "last_name"


@admin.register(Product)
class ProductAdmin(ModelAdmin):
    list_display = ("get_name", "sku", "main_supplier", "external_id", "dt_created")
    list_filter = ("dt_created",)
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
            _("Идентификаторы и логистика"),
            {
                "fields": (("sku", "external_id"), "main_supplier"),
            },
        ),
        (
            _("Даты"),
            {
                "fields": ("dt_created", "dt_updated"),
                "classes": ("collapse",),
            },
        ),
    )

    readonly_fields = ("dt_created", "dt_updated")

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


@admin.register(ProductSupplier)
class ProductSupplierAdmin(ModelAdmin):
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
class BrandAdmin(ModelAdmin):
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
class BrandSupplierAdmin(ModelAdmin):
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
class WarehouseAdmin(ModelAdmin):
    # Используем названия полей именно из твоего последнего куска кода
    list_display = ["name", "settlement", "is_virtual"]
    list_filter = ["is_virtual", "settlement"]
    search_fields = ["name"]
