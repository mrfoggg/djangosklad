from django import forms
from django.contrib import admin
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_countries.widgets import CountrySelectWidget
from mptt.admin import DraggableMPTTAdmin
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.contrib.forms.widgets import WysiwygWidget

from .models import (
    Brand,
    BrandSupplier,
    Category,
    Contractor,
    ContractorBankAccount,
    ContractorLegalDetails,
    ContractorLink,
    Organization,
    Product,
    ProductSupplier,
    RetailStore,
    Settlement,
    SettlementType,
    Warehouse,
)

BASE_READONLY_DATES = ("created", "updated")


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
    list_display = ("name", "url", "description")
    search_fields = ("name", "description")
