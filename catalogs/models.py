from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_countries.fields import CountryField


class BaseModel(models.Model):
    dt_created = models.DateTimeField(
        verbose_name=_("Создан"), auto_now_add=True, db_index=True
    )
    dt_updated = models.DateTimeField(verbose_name=_("Изменен"), auto_now=True)

    class Meta:
        abstract = True


class Contractor(BaseModel):
    class LegalType(models.TextChoices):
        INDIVIDUAL = "IND", _("Физическое лицо")
        FOP = "FOP", _("ФОП")
        OTHER = "OTH", _("Организация (ООО, ПАО и т.д.)")
        HOLDING = "HLD", _("Холдинг / Группа компаний")  # Новый тип

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

    iban = models.CharField(max_length=34, blank=True, verbose_name=_("IBAN"))

    legal_address = models.TextField(blank=True, verbose_name=_("Юридический адрес"))

    class Meta:
        verbose_name = _("Официальные реквизиты")


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

    main_supplier = models.ForeignKey(
        "ProductSupplier",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="main_for_product",
        verbose_name=_("Основной поставщик"),
        help_text=_("Выберите из списка уже добавленных поставщиков"),
    )

    def __str__(self):
        if self.name:
            return self.name
        elif self.site_name:
            return f"site_name: {self.site_name}"
        return f"Product object ({self.id})"  # Фолбэк, если оба поля пустые

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

    class Meta:
        verbose_name = _("Бренд")
        verbose_name_plural = _("Бренды")


class BrandSupplier(BaseModel):
    brand = models.ForeignKey("Brand", on_delete=models.CASCADE)
    supplier = models.ForeignKey(
        "Contractor", on_delete=models.CASCADE, limit_choices_to={"is_supplier": True}
    )

    class Meta:
        # Важно для уникальности связей
        unique_together = ("brand", "supplier")
        verbose_name = _("Поставщик бренда")
        verbose_name_plural = _("Поставщики брендов")

    def __str__(self):
        return f"{self.supplier}"  # В списке выбора будет видно имя поставщика
