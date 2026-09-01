from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_countries.fields import CountryField
from mptt.models import MPTTModel, TreeForeignKey


class BaseModel(models.Model):
    created = models.DateTimeField(
        verbose_name=_("Создан"), auto_now_add=True, db_index=True
    )
    updated = models.DateTimeField(verbose_name=_("Изменен"), auto_now=True)

    class Meta:
        abstract = True


class Category(MPTTModel, BaseModel):
    name = models.CharField(_("Название"), max_length=255)
    parent = TreeForeignKey(
        "self",
        db_index=True,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
        verbose_name=_("Родительская категория"),
    )
    slug = models.SlugField(max_length=255, unique=True, verbose_name=_("Слаг (URL)"))

    class MPTTMeta:
        order_insertion_by = ["name"]

    class Meta:
        verbose_name = _("Категория")
        verbose_name_plural = _("Категории")

    def __str__(self):
        return self.name


class Contractor(BaseModel):
    class LegalType(models.TextChoices):
        INDIVIDUAL = "IND", _("Физическое лицо")
        FOP = "FOP", _("ФОП")
        OTHER = "OTH", _("Организация (ООО, ПАО и т.д.)")
        HOLDING = "HLD", _("Холдинг / Группа компаний")  # Новый тип

    class PriceType(models.TextChoices):
        SMALL_WHOLESALE = "small_wholesale", _("Мелкий опт")
        WHOLESALE = "wholesale", _("Опт")
        LARGE_WHOLESALE = "large_wholesale", _("Крупный опт")

    legal_type = models.CharField(
        max_length=3,
        choices=LegalType.choices,
        default=LegalType.INDIVIDUAL,
        verbose_name=_("Тип контрагента"),
    )

    # Для организаций/ФОП сюда пишем название или Фамилию
    last_name = models.CharField(
        max_length=150, verbose_name=_("Фамилия / Название"), db_index=True
    )
    first_name = models.CharField(max_length=150, blank=True, verbose_name=_("Имя"))
    middle_name = models.CharField(
        max_length=150, blank=True, verbose_name=_("Отчество")
    )

    # Доп. поле для типа организации (ООО, ЧП), если выбрано "Другое"
    ownership_type = models.CharField(
        max_length=20,
        blank=True,
        verbose_name=_("Аббревиатура типа"),
        help_text=_("Например: ООО, ПП, ПАО. Для ФОП заполнится автоматически."),
    )

    email = models.EmailField(blank=True, verbose_name=_("Email"))
    is_supplier = models.BooleanField(default=True, verbose_name=_("Поставщик"))
    is_customer = models.BooleanField(default=True, verbose_name=_("Покупатель"))
    is_manufacturer = models.BooleanField(default=True, verbose_name=_("Производитель"))

    parent_holding = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subsidiaries",
        # Теперь родителем может быть только тот, у кого тип "Холдинг"
        limit_choices_to={"legal_type": "HLD"},
        verbose_name=_("Входит в холдинг"),
    )

    use_usd_prices = models.BooleanField(
        default=False,
        verbose_name=_("Цены в USD"),
        help_text=_(
            "Если включено, цены в прайсах этого поставщика будут автоматически конвертироваться в UAH по указанному курсу"
        ),
    )

    usd_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("1.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
        verbose_name=_("Курс доллара (USD/UAH)"),
        help_text=_("Личный курс поставщика для конвертации цен в прайсах"),
    )
    default_price_type = models.CharField(
        max_length=20,
        choices=PriceType.choices,
        default=PriceType.WHOLESALE,
        verbose_name=_("Тип цены по умолчанию"),
    )
    primary_account = models.ForeignKey(
        "ContractorBankAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("Основной счет"),
    )
    comment = models.TextField(blank=True, verbose_name=_("Комментарий"))

    class Meta:
        verbose_name = _("Контрагент")
        verbose_name_plural = _("Контрагенты")

    def __str__(self):
        """Логика вывода имени в зависимости от типа"""
        if self.legal_type == self.LegalType.FOP:
            return f"ФОП {self.last_name} {self.first_name} {self.middle_name}".strip()

        if self.legal_type == self.LegalType.OTHER:
            prefix = self.ownership_type if self.ownership_type else _("Орг.")
            return f"{prefix} {self.last_name}"

        # Для обычного физлица
        return f"{self.last_name} {self.first_name} {self.middle_name}".strip()

    def clean(self):
        super().clean()

        # Если выбран тип "Организация", поле аббревиатуры (ООО, ЧП) обязательно
        if self.legal_type == self.LegalType.OTHER and not self.ownership_type:
            raise ValidationError(
                {
                    "ownership_type": _(
                        "Для типа 'Организация' необходимо указать аббревиатуру типа (например: ООО, ПП)."
                    )
                }
            )

        # Если контрагент сам является холдингом (HLD)
        if self.legal_type == self.LegalType.HOLDING:
            # И при этом у него заполнен родительский холдинг
            if self.parent_holding:
                raise ValidationError(
                    {
                        "parent_holding": _(
                            "Холдинг не может входить в другой холдинг. "
                            "Сначала измените тип контрагента или уберите родителя."
                        )
                    }
                )

    def save(self, *args, **kwargs):
        # Если не поставщик, то точно не производитель
        if not self.is_supplier:
            self.is_manufacturer = False
        # Запускаем валидацию перед сохранением
        self.full_clean()
        super().save(*args, **kwargs)


class ContractorLegalDetails(models.Model):
    contractor = models.OneToOneField(
        "Contractor",  # Исправил на Contractor, так как модель выше называется так
        on_delete=models.CASCADE,
        related_name="legal_details",
        verbose_name=_("Контрагент"),
    )

    inn = models.CharField(
        max_length=20, blank=True, verbose_name=_("ИНН / ЕГРПОУ"), db_index=True
    )

    legal_address = models.CharField(
        max_length=256, blank=True, verbose_name=_("Юридический адрес")
    )

    class Meta:
        verbose_name = _("Официальные реквизиты")


class BaseBankAccount(BaseModel):
    """Абстрактный класс для всех банковских реквизитов"""

    bank_name = models.CharField(max_length=255, verbose_name=_("Название банка"))
    mfo = models.CharField(max_length=6, blank=True, verbose_name=_("МФО"))
    currency = models.CharField(
        max_length=3,
        default="UAH",
        verbose_name=_("Валюта счета"),
    )
    iban = models.CharField(
        max_length=34, unique=True, verbose_name=_("IBAN"), db_index=True
    )
    is_default = models.BooleanField(default=False, verbose_name=_("Основной счет"))
    note = models.CharField(max_length=255, blank=True, verbose_name=_("Примечание"))

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.bank_name} ({self.iban[-8:]})"


class ContractorBankAccount(BaseBankAccount):
    """Счета поставщиков и клиентов"""

    contractor = models.ForeignKey(
        "Contractor",
        on_delete=models.CASCADE,
        related_name="bank_accounts",
        verbose_name=_("Контрагент"),
    )

    class Meta:
        verbose_name = _("Счет контрагента")
        verbose_name_plural = _("Счета контрагентов")


class OurBankAccount(BaseBankAccount):
    """Наши расчетные счета"""

    organization = models.ForeignKey(
        "Organization",
        on_delete=models.CASCADE,
        related_name="our_accounts",
        verbose_name=_("Наша организация"),
    )

    class Meta:
        verbose_name = _("Наш расчетный счет")
        verbose_name_plural = _("Наши расчетные счета")


class ContractorLink(models.Model):
    contractor = models.ForeignKey(
        Contractor,
        on_delete=models.CASCADE,
        related_name="links",
        verbose_name=_("Контрагент"),
    )
    name = models.CharField(
        _("Название"), max_length=100, help_text=_("Например: Сайт, Instagram, Прайс")
    )
    url = models.URLField(_("Ссылка"), max_length=500)

    class Meta:
        verbose_name = _("Ссылка")
        verbose_name_plural = _("Ссылки")

    def __str__(self):
        return f"{self.name}: {self.url}"


class Organization(BaseModel):  # Наследуемся от вашей BaseModel
    name = models.CharField(max_length=127, verbose_name=_("Название организации"))
    full_name = models.CharField(
        max_length=255, blank=True, verbose_name=_("Полное название")
    )
    inn = models.CharField(max_length=12, blank=True, verbose_name=_("ИНН"))
    is_default = models.BooleanField(
        default=False,
        verbose_name=_("Использовать по умолчанию"),
        help_text=_(
            "Эта организация будет автоматически выбираться в новых документах"
        ),
    )

    class Meta:
        verbose_name = _("Организация")
        verbose_name_plural = _("Организации")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Если эта организация устанавливается как "по умолчанию",
        # снимаем этот флаг у всех остальных организаций.
        if self.is_default:
            Organization.objects.filter(is_default=True).exclude(pk=self.pk).update(
                is_default=False
            )
        super().save(*args, **kwargs)


class MeasurementUnit(models.Model):
    code = models.CharField(max_length=20, unique=True, verbose_name=_("Код"))
    name = models.CharField(max_length=100, verbose_name=_("Название"))
    symbol = models.CharField(max_length=20, verbose_name=_("Обозначение"))
    decimal_places = models.PositiveSmallIntegerField(
        default=0,
        validators=[MaxValueValidator(6)],
        verbose_name=_("Знаков после запятой"),
        help_text=_("Определяет точность количества и шаг стрелок в форме заказа"),
    )

    class Meta:
        ordering = ("name",)
        verbose_name = _("Единица измерения")
        verbose_name_plural = _("Единицы измерения")

    def __str__(self):
        return self.symbol


def get_default_measurement_unit_id():
    unit, _was_created = MeasurementUnit.objects.get_or_create(
        code="pcs",
        defaults={
            "name": _("Штука"),
            "symbol": _("шт."),
            "decimal_places": 0,
        },
    )
    return unit.pk


class Product(BaseModel):
    name = models.CharField(max_length=255, blank=True, verbose_name=_("Название"))

    site_name = models.CharField(
        max_length=255, blank=True, verbose_name=_("Название на сайте")
    )

    fiscal_name = models.CharField(
        max_length=255, blank=True, verbose_name=_("Название для чеков")
    )

    sku = models.CharField(
        max_length=64,
        blank=True,
        unique=True,  # Артикул обычно уникален
        verbose_name=_("Артикул (SKU)"),
    )

    external_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        db_index=True,  # Обязательно для быстрого поиска при импорте
        verbose_name=_("ID в OpenCart"),
    )

    category = TreeForeignKey(
        "Category",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
        verbose_name=_("Категория"),
    )

    subcategories = models.ManyToManyField(
        "Category",
        blank=True,
        related_name="subcategory_products",
        verbose_name=_("Подкатегории"),
        help_text=_(
            "Сначала выберите основную категорию, чтобы увидеть список доступных подкатегорий"
        ),
    )

    brand = models.ForeignKey(
        "Brand",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
        verbose_name=_("Бренд"),
    )

    main_supplier = models.ForeignKey(
        "ProductSupplier",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="main_for_product",
        verbose_name=_("Основной поставщик"),
        help_text=_("Выберите из списка уже добавленных поставщиков"),
    )

    unit = models.ForeignKey(
        MeasurementUnit,
        on_delete=models.PROTECT,
        default=get_default_measurement_unit_id,
        related_name="products",
        verbose_name=_("Единица измерения"),
    )

    def __str__(self):
        if self.name:
            product_name = self.name
        elif self.site_name:
            product_name = f"site_name: {self.site_name}"
        else:
            product_name = f"Product object ({self.id})"

        return f"{product_name}, {self.unit.symbol}" if self.unit_id else product_name

    class Meta:
        verbose_name = _("Товар")
        verbose_name_plural = _("Товары")

    def clean(self):
        super().clean()
        # Проверяем, что хотя бы одно из двух полей заполнено
        if not self.name and not self.site_name:
            raise ValidationError(
                _(
                    'Необходимо заполнить хотя бы одно из полей: "Название" или "Название на сайте".'
                )
            )

    def save(self, *args, **kwargs):
        # Вызываем full_clean(), чтобы валидация clean() работала всегда,
        # даже если вы создаете объект через код, а не через форму
        self.full_clean()
        super().save(*args, **kwargs)


class ProductSupplier(BaseModel):
    product = models.ForeignKey(
        "Product", on_delete=models.CASCADE, verbose_name=_("Товар")
    )
    supplier = models.ForeignKey(
        "Contractor",
        on_delete=models.CASCADE,
        limit_choices_to={"is_supplier": True},
        verbose_name=_("Поставщик"),
    )
    supplier_sku = models.CharField(
        max_length=64,
        blank=True,
        verbose_name=_("Артикул поставщика"),
        help_text=_("Как этот товар называется/кодируется в базе поставщика"),
    )
    comment = models.CharField(
        max_length=255, blank=True, null=True, verbose_name=_("Комментарий")
    )

    class Meta:
        verbose_name = _("Поставщик товара")
        verbose_name_plural = _("Поставщики товара")
        unique_together = (
            "product",
            "supplier",
        )  # Один и тот же поставщик не может быть добавлен дважды к одному товару

    def __str__(self):
        return f"{self.supplier} -> {self.product} ({self.supplier_sku})"


class Brand(BaseModel):
    name = models.CharField(
        max_length=255, unique=True, verbose_name=_("Название бренда")
    )

    # Используем ManyToMany с параметром through
    suppliers = models.ManyToManyField(
        "Contractor",
        through="BrandSupplier",
        related_name="brands",
        verbose_name=_("Поставщики бренда"),
    )

    # Ссылаемся на промежуточную модель
    main_supplier = models.ForeignKey(
        "BrandSupplier",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="main_for_brand",
        verbose_name=_("Основной поставщик"),
        help_text=_("Выберите из списка уже добавленных поставщиков бренда"),
    )

    manufacturer = models.ForeignKey(
        "Contractor",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        limit_choices_to={"is_manufacturer": True},  # Фильтр прямо в БД
        verbose_name="Контрагент производителя",
    )

    class Meta:
        verbose_name = _("Бренд")
        verbose_name_plural = _("Бренды")

    def __str__(self):
        return f"{self.name}"


class BrandSupplier(BaseModel):
    brand = models.ForeignKey("Brand", on_delete=models.CASCADE)
    supplier = models.ForeignKey(
        "Contractor", on_delete=models.CASCADE, limit_choices_to={"is_supplier": True}
    )
    comment = models.CharField(
        max_length=255, blank=True, null=True, verbose_name=_("Комментарий")
    )

    class Meta:
        # Важно для уникальности связей
        unique_together = ("brand", "supplier")
        verbose_name = _("Поставщик бренда")
        verbose_name_plural = _("Поставщики брендов")

    def __str__(self):
        return f"{self.supplier}"  # В списке выбора будет видно имя поставщика


# --- ГЕОГРАФИЯ ---


class SettlementType(models.Model):
    """Тип населенного пункта (г., с., пгт)"""

    name = models.CharField(max_length=50, verbose_name=_("Название типа"))
    short_name = models.CharField(max_length=10, verbose_name=_("Сокращение"))

    class Meta:
        verbose_name = _("Тип населенного пункта")
        verbose_name_plural = _("Типы населенных пунктов")

    def __str__(self):
        return self.short_name


class Settlement(models.Model):
    """Населенный пункт с привязкой к стране и UUID Новой Почты"""

    name = models.CharField(max_length=255, verbose_name=_("Название"))
    settlement_type = models.ForeignKey(
        SettlementType,
        on_delete=models.PROTECT,
        related_name="settlements",
        verbose_name=_("Тип"),
    )
    country = CountryField(default="UA", verbose_name=_("Страна"))
    region = models.CharField(
        max_length=255, blank=True, verbose_name=_("Область/Район")
    )

    # Идентификатор для интеграции (например, Ref из Новой Почты)
    ref_uuid = models.UUIDField(
        null=True, blank=True, unique=True, verbose_name=_("UUID Новой Почты")
    )

    class Meta:
        verbose_name = _("Населенный пункт")
        verbose_name_plural = _("Населенные пункты")
        ordering = ["name"]

    def __str__(self):
        return f"{self.settlement_type}. {self.name} ({self.country.name})"


class Warehouse(BaseModel):
    """Складская точка (физическая или виртуальная)"""

    name = models.CharField(max_length=255, verbose_name=_("Название склада"))

    # Делаем null=True, чтобы база позволяла пустые значения для виртуальных складов
    settlement = models.ForeignKey(
        Settlement,
        on_delete=models.PROTECT,
        related_name="warehouses",
        verbose_name=_("Населенный пункт"),
        null=True,
        blank=True,
    )
    address = models.CharField(max_length=255, blank=True, verbose_name=_("Адрес"))

    is_virtual = models.BooleanField(
        default=False,
        verbose_name=_("Виртуальный склад"),
        help_text=_("Используется для дропшиппинга или учета остатков поставщика"),
    )

    class Meta:
        verbose_name = _("Склад")
        verbose_name_plural = _("Склады")

    def __str__(self):
        location = self.settlement if self.settlement else _("Без привязки к городу")
        virtual_tag = f" [{_('Виртуальный')}]" if self.is_virtual else ""
        return f"{self.name} ({location}){virtual_tag}"

    def clean(self):
        """Валидация обязательности города для реальных складов"""
        super().clean()
        if not self.is_virtual and not self.settlement:
            raise ValidationError(
                {"settlement": _("Населенный пункт обязателен для физического склада.")}
            )


class RetailStore(BaseModel):
    name = models.CharField(
        max_length=255,
        unique=True,
        verbose_name=_("Название"),
    )
    url = models.URLField(
        blank=True,
        verbose_name=_("URL"),
    )
    description = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Описание"),
    )
    is_default = models.BooleanField(
        default=False,
        verbose_name=_("Использовать по умолчанию"),
        help_text=_("Этот магазин будет автоматически выбираться в новых установках розничных цен"),
    )

    class Meta:
        verbose_name = _("Розничный магазин")
        verbose_name_plural = _("Розничные магазины")
        ordering = ("name",)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.is_default:
            RetailStore.objects.filter(is_default=True).exclude(pk=self.pk).update(
                is_default=False
            )
        super().save(*args, **kwargs)
