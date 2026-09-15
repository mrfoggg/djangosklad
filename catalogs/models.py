from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import get_language, gettext_lazy as _
from django_countries.fields import CountryField
from mptt.models import MPTTModel, TreeForeignKey
from phonenumber_field.modelfields import PhoneNumberField
from phonenumbers import PhoneNumberType, carrier, geocoder, number_type


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
    phones = models.ManyToManyField("PhoneNumber", through="ContractorPhone", verbose_name=_("Телефоны"))
    contact_persons = models.ManyToManyField("ContactPerson", through="ContractorContactPerson", verbose_name=_("Контактные лица"))

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


class NovaPoshtaCatalog(BaseModel):
    is_active = models.BooleanField(default=True, verbose_name=_("Активен"))

    class Meta:
        abstract = True


class NovaPoshtaArea(NovaPoshtaCatalog):
    ref = models.UUIDField(primary_key=True, verbose_name=_("Ref Новой почты"))
    description = models.CharField(max_length=255, verbose_name=_("Название"))

    class Meta:
        verbose_name = _("Новая почта: область")
        verbose_name_plural = _("Новая почта: области")
        ordering = ("description",)

    def __str__(self):
        return self.description


class NovaPoshtaRegion(NovaPoshtaCatalog):
    ref = models.UUIDField(primary_key=True, verbose_name=_("Ref Новой почты"))
    area = models.ForeignKey(
        NovaPoshtaArea,
        on_delete=models.PROTECT,
        related_name="regions",
        verbose_name=_("Область"),
    )
    description = models.CharField(max_length=255, verbose_name=_("Название"))
    region_type = models.CharField(max_length=100, verbose_name=_("Тип района"))

    class Meta:
        verbose_name = _("Новая почта: район")
        verbose_name_plural = _("Новая почта: районы")
        ordering = ("area__description", "description")

    def __str__(self):
        return self.description


class NovaPoshtaSettlementType(NovaPoshtaCatalog):
    ref = models.UUIDField(primary_key=True, verbose_name=_("Ref Новой почты"))
    description = models.CharField(max_length=100, verbose_name=_("Название"))
    code = models.CharField(max_length=20, blank=True, verbose_name=_("Сокращение"))

    class Meta:
        verbose_name = _("Новая почта: тип населённого пункта")
        verbose_name_plural = _("Новая почта: типы населённых пунктов")
        ordering = ("description",)

    def __str__(self):
        return self.description


class NovaPoshtaSettlement(NovaPoshtaCatalog):
    ref = models.UUIDField(primary_key=True, verbose_name=_("Ref Новой почты"))
    area = models.ForeignKey(
        NovaPoshtaArea, on_delete=models.PROTECT,
        related_name="settlements", verbose_name=_("Область"),
    )
    region = models.ForeignKey(
        NovaPoshtaRegion, on_delete=models.PROTECT, null=True, blank=True,
        related_name="settlements", verbose_name=_("Район"),
    )
    settlement_type = models.ForeignKey(
        NovaPoshtaSettlementType, on_delete=models.PROTECT,
        related_name="settlements", verbose_name=_("Тип населённого пункта"),
    )
    description = models.CharField(max_length=255, verbose_name=_("Название"))
    description_ru = models.CharField(max_length=255, blank=True, verbose_name=_("Название на русском"))
    description_translit = models.CharField(max_length=255, blank=True, verbose_name=_("Название латиницей"))
    settlement_type_description = models.CharField(max_length=100, verbose_name=_("Тип населённого пункта"))
    settlement_type_description_ru = models.CharField(max_length=100, blank=True, verbose_name=_("Тип на русском"))
    settlement_type_description_translit = models.CharField(max_length=100, blank=True, verbose_name=_("Тип латиницей"))
    latitude = models.DecimalField(
        max_digits=18, decimal_places=15, null=True, blank=True,
        validators=[MinValueValidator(Decimal("-90")), MaxValueValidator(Decimal("90"))],
        verbose_name=_("Широта"),
    )
    longitude = models.DecimalField(
        max_digits=18, decimal_places=15, null=True, blank=True,
        validators=[MinValueValidator(Decimal("-180")), MaxValueValidator(Decimal("180"))],
        verbose_name=_("Долгота"),
    )
    index_1 = models.CharField(max_length=20, blank=True, verbose_name=_("Начальный индекс"))
    index_2 = models.CharField(max_length=20, blank=True, verbose_name=_("Конечный индекс"))
    index_coatsu_1 = models.CharField(max_length=20, blank=True, verbose_name=_("Код КОАТУУ"))
    delivery_1 = models.BooleanField(null=True, blank=True, verbose_name=_("Доставка: понедельник"))
    delivery_2 = models.BooleanField(null=True, blank=True, verbose_name=_("Доставка: вторник"))
    delivery_3 = models.BooleanField(null=True, blank=True, verbose_name=_("Доставка: среда"))
    delivery_4 = models.BooleanField(null=True, blank=True, verbose_name=_("Доставка: четверг"))
    delivery_5 = models.BooleanField(null=True, blank=True, verbose_name=_("Доставка: пятница"))
    delivery_6 = models.BooleanField(null=True, blank=True, verbose_name=_("Доставка: суббота"))
    delivery_7 = models.BooleanField(null=True, blank=True, verbose_name=_("Доставка: воскресенье"))
    special_cash_check = models.BooleanField(null=True, blank=True, verbose_name=_("SpecialCashCheck"))
    radius_home_delivery = models.PositiveIntegerField(null=True, blank=True, verbose_name=_("Радиус адресной доставки"))
    radius_express_pick_up = models.PositiveIntegerField(null=True, blank=True, verbose_name=_("Радиус экспресс-забора"))
    radius_drop = models.PositiveIntegerField(null=True, blank=True, verbose_name=_("Радиус сдачи отправлений"))
    warehouse = models.BooleanField(null=True, blank=True, verbose_name=_("Есть отделения"))
    address_delivery_allowed = models.BooleanField(null=True, blank=True, verbose_name=_("Адресная доставка доступна"))

    class Meta:
        verbose_name = _("Новая почта: населённый пункт")
        verbose_name_plural = _("Новая почта: населённые пункты")
        ordering = ("description", "ref")

    def __str__(self):
        return f"{self.settlement_type_description} {self.description}"

    def clean(self):
        super().clean()
        if self.region_id and self.area_id and self.region.area_id != self.area_id:
            raise ValidationError({"region": _("Район не относится к выбранной области.")})


class NovaPoshtaSettlementDetails(BaseModel):
    settlement = models.OneToOneField(
        NovaPoshtaSettlement, primary_key=True, on_delete=models.CASCADE,
        related_name="details", verbose_name=_("Населённый пункт"),
    )
    address_delivery_allowed = models.BooleanField(verbose_name=_("Адресная доставка доступна"))
    streets_availability = models.BooleanField(verbose_name=_("Улицы доступны"))
    delivery_city_ref = models.UUIDField(verbose_name=_("Ref города доставки"))
    description = models.CharField(max_length=255, verbose_name=_("Город доставки"))
    settlement_type = models.UUIDField(verbose_name=_("Ref типа города доставки"))
    settlement_type_description = models.CharField(max_length=100, verbose_name=_("Тип города доставки"))
    prevent_entry_new_streets_user = models.BooleanField(verbose_name=_("Запрет ввода новых улиц"))
    city_id = models.CharField(max_length=30, verbose_name=_("CityID"))
    special_cash_check = models.BooleanField(verbose_name=_("SpecialCashCheck"))
    area_description = models.CharField(max_length=255, verbose_name=_("Область города доставки"))
    delivery_1 = models.BooleanField(verbose_name=_("Доставка: понедельник"))
    delivery_2 = models.BooleanField(verbose_name=_("Доставка: вторник"))
    delivery_3 = models.BooleanField(verbose_name=_("Доставка: среда"))
    delivery_4 = models.BooleanField(verbose_name=_("Доставка: четверг"))
    delivery_5 = models.BooleanField(verbose_name=_("Доставка: пятница"))
    delivery_6 = models.BooleanField(verbose_name=_("Доставка: суббота"))
    delivery_7 = models.BooleanField(verbose_name=_("Доставка: воскресенье"))

    class Meta:
        verbose_name = _("Допданные Новой почты")
        verbose_name_plural = _("Допданные Новой почты")

    def __str__(self):
        return self.description


class DeliveryMethod(BaseModel):
    class Kind(models.TextChoices):
        CARRIER = "carrier", _("Перевозчик")
        PICKUP = "pickup", _("Самовывоз")

    name = models.CharField(_("Название"), max_length=100, unique=True)
    kind = models.CharField(_("Тип доставки"), max_length=20, choices=Kind.choices, default=Kind.CARRIER)

    class Meta:
        verbose_name = _("Способ доставки")
        verbose_name_plural = _("Способы доставки")
        ordering = ("name",)

    def __str__(self):
        return self.name


class PhoneNumber(BaseModel):
    number = PhoneNumberField(_("Номер телефона"), unique=True)
    has_viber = models.BooleanField(_("Viber"), null=True, blank=True, default=None)
    has_telegram = models.BooleanField(_("Telegram"), null=True, blank=True, default=None)
    has_whatsapp = models.BooleanField(_("WhatsApp"), null=True, blank=True, default=None)

    class Meta:
        verbose_name = _("Телефонный номер")
        verbose_name_plural = _("Телефонные номера")
        ordering = ("number",)

    def __str__(self):
        return self.number.as_international

    @property
    def operator_name(self):
        """Исходный оператор по справочнику кодов; перенос номера не учитывается."""
        if not self.number or not self.number.is_valid():
            return ""
        return carrier.name_for_number(self.number, "en")

    @property
    def number_type_label(self):
        if not self.number or not self.number.is_valid():
            return ""
        labels = {
            PhoneNumberType.FIXED_LINE: _("Стационарный"),
            PhoneNumberType.MOBILE: _("Мобильный"),
            PhoneNumberType.FIXED_LINE_OR_MOBILE: _("Стационарный или мобильный"),
            PhoneNumberType.TOLL_FREE: _("Бесплатный"),
            PhoneNumberType.PREMIUM_RATE: _("С повышенной тарификацией"),
            PhoneNumberType.SHARED_COST: _("С разделением стоимости"),
            PhoneNumberType.VOIP: _("IP-телефония"),
            PhoneNumberType.PERSONAL_NUMBER: _("Персональный номер"),
            PhoneNumberType.PAGER: _("Пейджер"),
            PhoneNumberType.UAN: _("Универсальный номер доступа"),
            PhoneNumberType.VOICEMAIL: _("Голосовая почта"),
        }
        return labels.get(number_type(self.number), _("Неизвестно"))

    @property
    def region_description(self):
        """Географическая привязка кода, а не текущее местоположение абонента."""
        if not self.number or not self.number.is_valid():
            return ""
        language = (get_language() or "en").split("-")[0]
        return geocoder.description_for_number(self.number, language)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class ContactPerson(BaseModel):
    last_name = models.CharField(_("Фамилия"), max_length=150)
    first_name = models.CharField(_("Имя"), max_length=150, blank=True)
    middle_name = models.CharField(_("Отчество"), max_length=150, blank=True)
    email = models.EmailField(_("Email"), blank=True)
    comment = models.TextField(_("Комментарий"), blank=True)
    phones = models.ManyToManyField(PhoneNumber, through="ContactPersonPhone", verbose_name=_("Телефоны"))

    class Meta:
        verbose_name = _("Контактное лицо")
        verbose_name_plural = _("Контактные лица")
        ordering = ("last_name", "first_name", "pk")

    def __str__(self):
        return " ".join(filter(None, (self.last_name, self.first_name, self.middle_name)))


class ContactRole(models.Model):
    code = models.SlugField(_("Код"), unique=True)
    name = models.CharField(_("Название"), max_length=150)

    class Meta:
        verbose_name = _("Роль контактного лица")
        verbose_name_plural = _("Роли контактных лиц")
        ordering = ("name",)

    def __str__(self):
        return self.name


class ContractorContactPerson(BaseModel):
    contractor = models.ForeignKey(Contractor, on_delete=models.CASCADE, related_name="contact_links", verbose_name=_("Контрагент"))
    contact_person = models.ForeignKey(ContactPerson, on_delete=models.CASCADE, related_name="contractor_links", verbose_name=_("Контактное лицо"))
    roles = models.ManyToManyField(ContactRole, verbose_name=_("Роли"), blank=True)
    note = models.CharField(_("Примечание"), max_length=150, blank=True)

    class Meta:
        verbose_name = _("Контакт контрагента")
        verbose_name_plural = _("Контакты контрагента")
        constraints = [models.UniqueConstraint(fields=("contractor", "contact_person"), name="unique_contractor_contact")]

    def __str__(self):
        return f"{self.contractor}: {self.contact_person}"


class BasePhoneLink(BaseModel):
    phone = models.ForeignKey(PhoneNumber, on_delete=models.PROTECT, verbose_name=_("Телефон"))
    for_communication = models.BooleanField(_("Для связи"), default=True)
    for_delivery = models.BooleanField(_("Для доставки"), default=False)
    is_primary_for_communication = models.BooleanField(_("Основной для связи"), default=False)
    is_primary_for_delivery = models.BooleanField(_("Основной для доставки"), default=False)
    label = models.CharField(_("Подпись"), max_length=150, blank=True)
    comment = models.TextField(_("Комментарий"), blank=True)

    class Meta:
        abstract = True
        constraints = [
            models.CheckConstraint(condition=models.Q(is_primary_for_communication=False) | models.Q(for_communication=True), name="%(class)s_primary_comm_purpose"),
            models.CheckConstraint(condition=models.Q(is_primary_for_delivery=False) | models.Q(for_delivery=True), name="%(class)s_primary_delivery_purpose"),
        ]

    def __str__(self):
        return str(self.phone)


def phone_link_constraints(owner):
    return [
        models.UniqueConstraint(fields=(owner, "phone"), name=f"unique_{owner}_phone"),
        models.UniqueConstraint(fields=(owner,), condition=models.Q(is_primary_for_communication=True), name=f"unique_{owner}_primary_comm"),
        models.UniqueConstraint(fields=(owner,), condition=models.Q(is_primary_for_delivery=True), name=f"unique_{owner}_primary_delivery"),
    ]


class ContractorPhone(BasePhoneLink):
    contractor = models.ForeignKey(Contractor, on_delete=models.CASCADE, related_name="phone_links", verbose_name=_("Контрагент"))

    class Meta(BasePhoneLink.Meta):
        abstract = False
        verbose_name = _("Телефон контрагента")
        verbose_name_plural = _("Телефоны контрагента")
        constraints = BasePhoneLink.Meta.constraints + phone_link_constraints("contractor")


class ContactPersonPhone(BasePhoneLink):
    contact_person = models.ForeignKey(ContactPerson, on_delete=models.CASCADE, related_name="phone_links", verbose_name=_("Контактное лицо"))

    class Meta(BasePhoneLink.Meta):
        abstract = False
        verbose_name = _("Телефон контактного лица")
        verbose_name_plural = _("Телефоны контактного лица")
        constraints = BasePhoneLink.Meta.constraints + phone_link_constraints("contact_person")


class NovaPoshtaWarehouse(NovaPoshtaCatalog):
    ref = models.UUIDField(primary_key=True, verbose_name=_("Ref Новой почты"))
    settlement = models.ForeignKey(
        NovaPoshtaSettlement, on_delete=models.PROTECT, null=True, blank=True,
        related_name="warehouses", verbose_name=_("Населённый пункт"),
    )
    settlement_ref = models.UUIDField(null=True, blank=True, db_index=True, verbose_name=_("SettlementRef источника"))
    city_ref = models.UUIDField(null=True, blank=True, verbose_name=_("Ref города доставки"))
    warehouse_type_ref = models.UUIDField(verbose_name=_("Ref типа отделения"))
    number = models.CharField(_("Номер"), max_length=30)
    description = models.CharField(_("Название"), max_length=1000)
    description_ru = models.CharField(_("Название на русском"), max_length=1000, blank=True)
    short_address = models.CharField(_("Краткий адрес"), max_length=1000, blank=True)
    phone = models.CharField(_("Телефон"), max_length=100, blank=True)
    category = models.CharField(_("Категория"), max_length=100, blank=True, db_index=True)
    status = models.CharField(_("Статус Новой почты"), max_length=100, blank=True)
    postal_code = models.CharField(_("Почтовый индекс"), max_length=20, blank=True)
    settlement_description = models.CharField(_("Населённый пункт источника"), max_length=255, blank=True)
    latitude = models.DecimalField(_("Широта"), max_digits=18, decimal_places=15, null=True, blank=True, validators=[MinValueValidator(Decimal("-90")), MaxValueValidator(Decimal("90"))])
    longitude = models.DecimalField(_("Долгота"), max_digits=18, decimal_places=15, null=True, blank=True, validators=[MinValueValidator(Decimal("-180")), MaxValueValidator(Decimal("180"))])
    total_max_weight = models.DecimalField(_("Максимальный вес отправления, кг"), max_digits=12, decimal_places=3, null=True, blank=True)
    place_max_weight = models.DecimalField(_("Максимальный вес места, кг"), max_digits=12, decimal_places=3, null=True, blank=True)
    schedule = models.JSONField(_("График работы"), default=dict, blank=True)
    reception = models.JSONField(_("График приёма"), default=dict, blank=True)
    delivery = models.JSONField(_("График выдачи / доставки"), default=dict, blank=True)
    sending_dimensions = models.JSONField(_("Ограничения габаритов отправки"), default=dict, blank=True)
    receiving_dimensions = models.JSONField(_("Ограничения габаритов получения"), default=dict, blank=True)
    raw_data = models.JSONField(_("Все данные Новой почты"), default=dict, blank=True)

    site_key = models.CharField(_("SiteKey Новой почты"), max_length=1000, blank=True, db_index=True)
    short_address_ru = models.CharField(_("Краткий адрес на русском"), max_length=1000, blank=True)
    city_description = models.CharField(_("Город доставки"), max_length=1000, blank=True)
    city_description_ru = models.CharField(_("Город доставки на русском"), max_length=1000, blank=True)
    settlement_area_description = models.CharField(_("Область источника"), max_length=1000, blank=True)
    settlement_regions_description = models.CharField(_("Район источника"), max_length=1000, blank=True)
    settlement_type_description = models.CharField(_("Тип населённого пункта"), max_length=1000, blank=True)
    settlement_type_description_ru = models.CharField(_("Тип населённого пункта на русском"), max_length=1000, blank=True)
    district_code = models.CharField(_("Код подразделения (DistrictCode)"), max_length=1000, blank=True)
    status_date = models.CharField(_("Дата статуса в источнике"), max_length=1000, blank=True)
    direct = models.CharField(_("Направление (Direct)"), max_length=1000, blank=True)
    region_city = models.CharField(_("Региональный центр"), max_length=1000, blank=True)
    post_machine_type = models.CharField(_("Режим почтомата"), max_length=1000, blank=True)
    postomat_for = models.CharField(_("Назначение почтомата"), max_length=1000, blank=True)
    warehouse_index = models.CharField(_("Индекс отделения"), max_length=1000, blank=True)
    beacon_code = models.CharField(_("BeaconCode"), max_length=1000, blank=True)
    location = models.CharField(_("Расположение (Location)"), max_length=1000, blank=True)
    post_finance = models.BooleanField(_("Финансовые услуги (PostFinance)"), null=True, blank=True)
    bicycle_parking = models.BooleanField(_("Велопарковка"), null=True, blank=True)
    payment_access = models.BooleanField(_("Доступность оплаты (PaymentAccess)"), null=True, blank=True)
    pos_terminal = models.BooleanField(_("POS-терминал"), null=True, blank=True)
    international_shipping = models.BooleanField(_("Международные отправления"), null=True, blank=True)
    warehouse_illusha = models.BooleanField(_("WarehouseIllusha"), null=True, blank=True)
    warehouse_for_agent = models.BooleanField(_("Отделение для агента"), null=True, blank=True)
    generator_enabled = models.BooleanField(_("Генератор"), null=True, blank=True)
    work_in_mobile_awis = models.BooleanField(_("Работа в мобильном AWIS"), null=True, blank=True)
    deny_to_select = models.BooleanField(_("Запрещён выбор отделения"), null=True, blank=True)
    can_get_money_transfer = models.BooleanField(_("Получение денежных переводов"), null=True, blank=True)
    has_mirror = models.BooleanField(_("Зеркало"), null=True, blank=True)
    has_fitting_room = models.BooleanField(_("Примерочная"), null=True, blank=True)
    only_receiving_parcel = models.BooleanField(_("Только получение посылок"), null=True, blank=True)
    self_service_workplaces_count = models.PositiveIntegerField(_("Рабочие места самообслуживания"), null=True, blank=True)
    max_declared_cost = models.DecimalField(_("Максимальная объявленная стоимость"), max_digits=15, decimal_places=2, null=True, blank=True)

    class Meta:
        verbose_name = _("Новая почта: отделение / почтомат")
        verbose_name_plural = _("Новая почта: отделения и почтоматы")
        ordering = ("description", "ref")

    def __str__(self):
        return self.description
