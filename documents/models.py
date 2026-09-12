from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, GeneratedField
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from djmoney.models.fields import MoneyField
from unfold.views import BaseAutocompleteView

from catalogs.models import Contractor, Organization, RetailStore


def get_default_organization_id():
    # Используем .values_list, чтобы вытащить только ID и не грузить весь объект
    org_id = (
        Organization.objects.filter(is_default=True)
        .values_list("id", flat=True)
        .first()
    )
    return org_id


def get_default_retail_store_id():
    return (
        RetailStore.objects.filter(is_default=True)
        .values_list("id", flat=True)
        .first()
    )


class BaseDocumentModel(models.Model):
    """
    Абстрактная база для всех документов.
    """

    created = models.DateTimeField(
        verbose_name=_("Создан"), auto_now_add=True, db_index=True
    )
    updated = models.DateTimeField(verbose_name=_("Изменен"), auto_now=True)

    organization = models.ForeignKey(
        Organization,
        default=get_default_organization_id,
        on_delete=models.PROTECT,  # Для документов лучше PROTECT, чтобы не удалить юрлицо с историей
        verbose_name=_("Организация"),
    )

    # Флаг проведения документа
    is_applied = models.BooleanField(
        verbose_name=_("Проведен"),
        default=False,
        db_index=True,
    )

    to_remove = models.BooleanField(
        verbose_name=_("Помечен на удаление"), default=False, db_index=True
    )

    dt_applied = models.DateTimeField(
        verbose_name=_("Дата проведения"), null=True, blank=True
    )

    def save(self, *args, **kwargs):
        # Извлекаем наш флаг из памяти объекта (его туда положила админка)
        force_now = getattr(self, "_force_current_date", False)
        print("Save called, force_now:", force_now)

        if self.is_applied:
            # Условие: ставим текущую дату если:
            # 1. Даты еще нет (первичное проведение)
            # 2. ИЛИ пользователь нажал "Провести оперативно"
            if not self.dt_applied or force_now:
                self.dt_applied = timezone.now()
                print("FORCE NOW:", self.dt_applied)

        elif not self.is_applied and self.dt_applied:
            # Если флаг снят — очищаем дату
            self.dt_applied = None

        super().save(*args, **kwargs)

    class Meta:
        abstract = True
        ordering = ["-created"]

    def __str__(self):
        # Форматируем даты заранее для удобства
        created_str = self.created.strftime("%d.%m.%Y")

        if self.is_applied:
            # Если дата проведения по какой-то причине пуста (редкий случай),
            # подстрахуемся и выведем хотя бы дату создания
            applied_str = (
                self.dt_applied.strftime("%d.%m.%Y") if self.dt_applied else created_str
            )
            return f"{self._meta.verbose_name} №{self.id} (Проведен {applied_str})"

        # Если не проведен — выводим статус Черновик и дату создания
        return f"{self._meta.verbose_name} №{self.id} от {created_str} (Черновик)"


class SupplierPriceList(BaseDocumentModel):
    supplier = models.ForeignKey(
        "catalogs.Contractor",  # Ссылка на Contractor в catalogs
        on_delete=models.CASCADE,
        limit_choices_to={"is_supplier": True},
        verbose_name=_("Поставщик"),
        related_name="price_lists",
    )
    # Переопределяем поле базовой модели специально для прайса
    organization = models.ForeignKey(
        Organization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Организация"),
        help_text=_("Оставьте пустым, если цена общая для всех организаций"),
    )
    comment = models.TextField(_("Комментарий"), blank=True)

    class Meta(BaseDocumentModel.Meta):
        verbose_name = _("Установка цен поставщика")
        verbose_name_plural = _("Установки цен поставщика")


class SupplierPriceItem(models.Model):
    document = models.ForeignKey(
        "SupplierPriceList",
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name=_("Документ"),
    )
    product = models.ForeignKey(
        "catalogs.Product",  # Исправлено: теперь тоже из catalogs
        on_delete=models.PROTECT,
        verbose_name=_("Товар"),
    )
    price = MoneyField(
        max_digits=12,
        decimal_places=2,
        # default_currency="UAH",
        # currency_choices=AVAILABLE_CURRENCIES,
        verbose_name=_("Цена"),
    )
    small_wholesale_price = MoneyField(
        max_digits=12,
        decimal_places=2,
        default=0,
        verbose_name=_("Мелкий опт"),
    )
    wholesale_price = MoneyField(
        max_digits=12,
        decimal_places=2,
        default=0,
        verbose_name=_("Опт"),
    )
    large_wholesale_price = MoneyField(
        max_digits=12,
        decimal_places=2,
        default=0,
        verbose_name=_("Крупный опт"),
    )

    created = models.DateTimeField(
        verbose_name=_("Создан"), auto_now_add=True, db_index=True
    )
    updated = models.DateTimeField(verbose_name=_("Изменен"), auto_now=True)

    class Meta:
        verbose_name = _("Позиция прайса")
        verbose_name_plural = _("Позиции прайса")


class RetailPriceList(BaseDocumentModel):
    retail_store = models.ForeignKey(
        "catalogs.RetailStore",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        default=get_default_retail_store_id,
        related_name="price_lists",
        verbose_name=_("Розничный магазин"),
        help_text=_("Оставьте пустым, если прайс действует для всех магазинов"),
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Организация"),
        help_text=_("Оставьте пустым, если цена общая для всех организаций"),
    )
    comment = models.TextField(_("Комментарий"), blank=True)

    class Meta(BaseDocumentModel.Meta):
        verbose_name = _("Установка розничных цен")
        verbose_name_plural = _("Установки розничных цен")


class RetailPriceItem(models.Model):
    document = models.ForeignKey(
        "RetailPriceList",
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name=_("Документ"),
    )
    product = models.ForeignKey(
        "catalogs.Product",
        on_delete=models.PROTECT,
        verbose_name=_("Товар"),
    )
    price = MoneyField(
        max_digits=12,
        decimal_places=2,
        verbose_name=_("Розничная цена"),
    )
    created = models.DateTimeField(
        verbose_name=_("Создан"), auto_now_add=True, db_index=True
    )
    updated = models.DateTimeField(verbose_name=_("Изменен"), auto_now=True)

    class Meta:
        verbose_name = _("Позиция розничного прайса")
        verbose_name_plural = _("Позиции розничного прайса")


class PurchaseOrder(BaseDocumentModel):
    """
    Документ: Заказ поставщику.
    """

    supplier = models.ForeignKey(
        "catalogs.Contractor",
        on_delete=models.CASCADE,
        limit_choices_to={"is_supplier": True},
        verbose_name=_("Поставщик"),
        related_name="purchase_orders",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Организация"),
        help_text=_("Оставьте пустым, если в будут использоваться разные организации"),
    )
    comment = models.TextField(_("Комментарий"), blank=True)
    price_type = models.CharField(
        max_length=20,
        choices=Contractor.PriceType.choices,
        null=True,
        blank=True,
        verbose_name=_("Тип цены"),
        help_text=_("Если не выбран, используется тип цены по умолчанию у поставщика"),
    )

    def validate_receipt_lock(self):
        if not self.pk or not GoodsReceiptItem.objects.filter(
            order_item__purchase_order_id=self.pk, receipt__is_applied=True,
        ).exists():
            return
        previous = type(self).objects.get(pk=self.pk)
        fields = ("is_applied", "dt_applied", "supplier_id", "organization_id", "to_remove", "price_type")
        if getattr(self, "_force_current_date", False) or any(
            getattr(self, field) != getattr(previous, field) for field in fields
        ):
            raise ValidationError(_("Сначала снимите проведение связанных поступлений: изменение реквизитов и перепроведение заказа заблокированы."))

    def clean(self):
        super().clean()
        self.validate_receipt_lock()

    def save(self, *args, **kwargs):
        self.validate_receipt_lock()
        if not self.price_type and self.supplier_id:
            self.price_type = self.supplier.default_price_type
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Заказ №{self.id} поставщику {self.supplier} от {self.created.strftime('%Y-%m-%d')}"

    class Meta(BaseDocumentModel.Meta):
        verbose_name = _("Заказ поставщику")
        verbose_name_plural = _("Заказы поставщикам")


class CustomerOrder(BaseDocumentModel):
    STATUS_CHOICES = [
        ("new", _("Новый")),
        ("confirmed", _("Подтвержден")),
        ("done", _("Выполнен")),
        ("canceled", _("Отменен")),
    ]

    # Используем существующую модель Contractor
    customer = models.ForeignKey(
        "catalogs.Contractor",
        on_delete=models.PROTECT,
        related_name="customer_orders",
        limit_choices_to={"is_customer": True},
        verbose_name=_("Покупатель"),
    )
    retail_store = models.ForeignKey(
        RetailStore,
        default=get_default_retail_store_id,
        on_delete=models.PROTECT,
        related_name="customer_orders",
        verbose_name=_("Розничный магазин"),
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="new", verbose_name=_("Статус")
    )
    comment = models.TextField(blank=True, null=True, verbose_name=_("Комментарий"))

    class Meta:
        verbose_name = _("Заказ покупателя")
        verbose_name_plural = _("Заказы покупателей")
        ordering = ["-created"]

    def __str__(self):
        # Используем метод или атрибут имени у Contractor (например, name или last_name)
        return f"{_('Заказ')} №{self.id} - {self.customer}"


class OrderItem(models.Model):
    """Строка товара, универсальная для закупок и продаж. Позволяет реализовать механим резервирования"""

    class CustomerPaymentMethod(models.TextChoices):
        PREPAID = "prepaid", _("Оплата по счету")
        POSTPAID = "postpaid", _("Постоплата")

    product = models.ForeignKey(
        "catalogs.Product",
        on_delete=models.PROTECT,
        related_name="order_items",
        verbose_name=_("Товар"),
    )
    # КОЛИЧЕСТВО И ЦЕНЫ
    purchase_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("Закупочная цена"),
    )
    customer_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("Цена продажи"),
    )
    rrp = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("РРЦ"),
    )
    quantity = models.DecimalField(
        max_digits=14,
        decimal_places=6,
        default=1,
        verbose_name=_("Количество"),
    )
    # ВЫЧИСЛЯЕМЫЕ СУММЫ
    purchase_total_price = models.GeneratedField(
        expression=F("quantity") * F("purchase_price"),
        output_field=models.DecimalField(max_digits=10, decimal_places=2, null=True),
        db_persist=True,
        verbose_name=_("Сумма закупки"),
    )
    customer_total_price = models.GeneratedField(
        expression=F("quantity") * F("customer_price"),
        output_field=models.DecimalField(max_digits=10, decimal_places=2, null=True),
        db_persist=True,
        verbose_name=_("Сумма продажи"),
    )
    # СВЯЗИ С ЗАКАЗАМИ И СКЛАДОМ
    purchase_order = models.ForeignKey(
        "PurchaseOrder",
        on_delete=models.CASCADE,
        related_name="items",
        null=True,
        blank=True,
        limit_choices_to={
            "to_remove": False,
        },
        verbose_name=_("Заказ поставщику"),
    )
    customer_order = models.ForeignKey(
        "CustomerOrder",
        on_delete=models.CASCADE,
        related_name="items",
        null=True,
        blank=True,
        limit_choices_to={
            "to_remove": False,
        },
        verbose_name=_("Заказ покупателя"),
    )
    warehouse = models.ForeignKey(
        "catalogs.Warehouse",
        on_delete=models.PROTECT,
        related_name="order_items",
        verbose_name=_("Склад"),
        help_text=_("Склад отгрузки или приемки для этой строки"),
    )

    organization = models.ForeignKey(
        "catalogs.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Организация"),
        help_text=_("Если не указано, берется из шапки заказа"),
    )

    # СОРТИРОВКА
    sort_order_purchase = models.PositiveIntegerField(
        default=0,
        null=True,
        blank=True,
        verbose_name=_("Порядок в закупке"),
        db_index=True,
    )
    sort_order_customer = models.PositiveIntegerField(
        default=0,
        null=True,
        blank=True,
        verbose_name=_("Порядок в продаже"),
        db_index=True,
    )

    created = models.DateTimeField(
        verbose_name=_("Создан"), auto_now_add=True, db_index=True
    )
    updated = models.DateTimeField(verbose_name=_("Изменен"), auto_now=True)

    # Условия оплаты для КЛИЕНТА (нам)
    payment_method_customer = models.CharField(
        max_length=20,
        choices=CustomerPaymentMethod.choices,
        default=CustomerPaymentMethod.PREPAID,
        verbose_name=_("Оплата покупателем"),
    )

    class Meta:
        verbose_name = _("Товар в заказе")
        verbose_name_plural = _("Товары в заказе")
        # По умолчанию сортируем по обоим полям (Django применит их по приоритету)
        ordering = ["sort_order_purchase", "sort_order_customer"]

    def __str__(self):
        return f"{self.product.name} ({self.quantity})"

    def validate_receipt_lock(self):
        if not self.pk or not self.receipt_items.filter(receipt__is_applied=True).exists():
            return
        previous = type(self).objects.get(pk=self.pk)
        fields = ("purchase_price", "product_id", "quantity", "organization_id", "purchase_order_id", "warehouse_id")
        if any(getattr(self, field) != getattr(previous, field) for field in fields):
            raise ValidationError(_("Строка заказа связана с проведённым поступлением. Сначала снимите проведение поступления."))

    def clean(self):
        super().clean()
        self.validate_receipt_lock()

    def save(self, *args, **kwargs):
        print("SAVE OrderItem")
        # Если организация в строке не указана, пытаемся взять ее из заказа
        if not self.organization:
            print("NO organization")
            # Проверяем наличие закупки или заказа покупателя
            parent_order = self.purchase_order or self.customer_order
            if parent_order and parent_order.organization:
                self.organization = parent_order.organization

        self.validate_receipt_lock()
        super().save(*args, **kwargs)

    # def delete(self, *args, **kwargs):
    #     if (self.purchase_order and self.purchase_order.is_applied) or \
    #         (self.customer_order and self.customer_order.is_applied):
    #         raise ValidationError("Нельзя удалить строку в проведенном документе.")
    #     super().delete(*args, **kwargs)


class PurchaseInvoice(BaseDocumentModel):
    supplier = models.ForeignKey(
        "catalogs.Contractor",
        on_delete=models.CASCADE,
        limit_choices_to={"is_supplier": True},
        verbose_name=_("Поставщик"),
        related_name="purchase_invoices",
    )
    bank_account = models.ForeignKey(
        "catalogs.ContractorBankAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("Счет для оплаты"),
    )
    note = models.CharField(_("Примечание"), max_length=255, blank=True)

    orders = models.ManyToManyField(
        "PurchaseOrder",
        related_name="invoices",
        blank=True,  # Разрешаем пустое поле при создании
        verbose_name=_("Основание: Заказы поставщику"),
        help_text=_(
            "Используется только для фильтрации доступных позиций в строках счета. "
            "Фактическая связь устанавливается на уровне конкретных товаров."
        ),
    )

    def clean(self):
        super().clean()

        # Проверка выполняется только при попытке провести документ
        if self.is_applied:
            # 1. Если счет не выбран вручную и у поставщика нет основного счета
            if not self.bank_account and not self.supplier.primary_account:
                # Проверяем, есть ли у него вообще хоть какие-то счета
                if not self.supplier.bank_accounts.exists():
                    raise ValidationError(
                        {
                            "is_applied": _(
                                "Невозможно провести счет — у поставщика не заведено ни одного банковского счета."
                            )
                        }
                    )
                else:
                    # Счета есть, но основной не выбран
                    raise ValidationError(
                        {
                            "bank_account": _(
                                "У поставщика есть счета, но не выбран основной. "
                                "Выберите счет вручную или установите основной счет в карточке контрагента."
                            )
                        }
                    )

    def save(self, *args, **kwargs):
        #  если счет не задан подставляем счет контрагент по умолчанию
        if not self.bank_account and self.supplier.primary_account:
            self.bank_account = self.supplier.primary_account

        super().save(*args, **kwargs)
        # self.full_clean()

    class Meta:
        verbose_name = _("Входящий счет")
        verbose_name_plural = _("Входящие счета")


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(
        "PurchaseInvoice", on_delete=models.CASCADE, related_name="items"
    )
    order_item = models.ForeignKey(
        "OrderItem",
        on_delete=models.PROTECT,  # Рекомендую PROTECT, чтобы случайно не "снести" строку в проведенном счете
        related_name="invoice_items",
        verbose_name=_("Строка заказа"),
    )
    quantity = models.DecimalField(
        _("Количество по счёту"), max_digits=14, decimal_places=6, blank=True,
        validators=[MinValueValidator(Decimal("0.000001"))],
    )

    @property
    def total_price(self):
        price = self.order_item.purchase_price
        if price is None or self.quantity is None:
            return None
        return (self.quantity * price).quantize(Decimal("0.01"))

    def save(self, *args, **kwargs):
        if self.quantity is None and self.order_item_id:
            from .invoices import with_invoice_balance
            self.quantity = with_invoice_balance(
                OrderItem.objects.filter(pk=self.order_item_id), self.invoice_id,
            ).get().invoice_remaining
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.order_item_id and self.quantity is not None:
            places = max(0, -self.quantity.normalize().as_tuple().exponent)
            if places > self.order_item.product.unit.decimal_places:
                raise ValidationError({"quantity": _("Количество не соответствует точности единицы измерения товара.")})

    sort_order = models.PositiveIntegerField(
        default=0, blank=True, null=True, verbose_name=_("Порядок"), db_index=True
    )

    class Meta:
        verbose_name = _("Позиция входящего счета")
        verbose_name_plural = _("Позиции входящего счета")
        ordering = ["sort_order"]
        constraints = [
            models.UniqueConstraint(fields=("invoice", "order_item"), name="unique_invoice_order_item"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="invoice_quantity_positive"),
        ]

    def __str__(self):
        return f"{self.invoice.id} [# {self.sort_order}] <- {self.order_item}"


class SalesInvoice(BaseDocumentModel):
    customer = models.ForeignKey(
        "catalogs.Contractor",
        on_delete=models.PROTECT,
        limit_choices_to={"is_customer": True},
        related_name="sales_invoices",
        verbose_name=_("Покупатель"),
    )
    bank_account = models.ForeignKey(
        "catalogs.OurBankAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales_invoices",
        verbose_name=_("Счет для оплаты"),
    )
    note = models.CharField(_("Примечание"), max_length=255, blank=True)
    orders = models.ManyToManyField(
        "CustomerOrder",
        related_name="sales_invoices",
        blank=True,
        verbose_name=_("Основание: Заказы покупателей"),
        help_text=_(
            "Используется для выбора доступных позиций. "
            "Фактическая связь устанавливается на уровне строк счета."
        ),
    )

    def clean(self):
        super().clean()
        if (
            self.bank_account_id
            and self.organization_id
            and self.bank_account.organization_id != self.organization_id
        ):
            raise ValidationError(
                {"bank_account": _("Банковский счет относится к другой организации.")}
            )
        if not self.is_applied or self.bank_account_id or not self.organization_id:
            return

        accounts = self.organization.our_accounts.all()
        if accounts.filter(is_default=True).exists():
            return
        if not accounts.exists():
            raise ValidationError(
                {
                    "is_applied": _(
                        "Невозможно провести счет — у организации нет банковского счета."
                    )
                }
            )
        raise ValidationError(
            {
                "bank_account": _(
                    "У организации есть банковские счета, но основной счет не выбран. "
                    "Выберите счет вручную или установите основной счет."
                )
            }
        )

    def save(self, *args, **kwargs):
        if not self.bank_account_id and self.organization_id:
            self.bank_account = self.organization.our_accounts.filter(
                is_default=True
            ).first()
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = _("Счет на оплату покупателю")
        verbose_name_plural = _("Счета на оплату покупателям")


class SalesInvoiceItem(models.Model):
    invoice = models.ForeignKey(
        SalesInvoice,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name=_("Счет на оплату покупателю"),
    )
    order_item = models.OneToOneField(
        OrderItem,
        on_delete=models.PROTECT,
        related_name="sales_invoice_item",
        verbose_name=_("Строка заказа"),
    )
    sort_order = models.PositiveIntegerField(
        default=0,
        blank=True,
        null=True,
        db_index=True,
        verbose_name=_("Порядок"),
    )

    class Meta:
        verbose_name = _("Позиция счета на оплату покупателю")
        verbose_name_plural = _("Позиции счета на оплату покупателю")
        ordering = ["sort_order"]

    def __str__(self):
        return f"{self.invoice.id} [# {self.sort_order}] <- {self.order_item}"


class BaseBankPayment(BaseDocumentModel):
    """Абстрактный класс для всех банковских платежей"""

    organization = models.ForeignKey(
        "catalogs.Organization",
        on_delete=models.PROTECT,
        verbose_name=_("Наша организация"),
    )
    contractor = models.ForeignKey(
        "catalogs.Contractor",
        on_delete=models.PROTECT,
        verbose_name=_("Контрагент"),
    )
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, verbose_name=_("Сумма")
    )
    # our_bank_account = models.ForeignKey(
    #     "catalogs.OurBankAccount",
    #     on_delete=models.PROTECT,
    #     verbose_name=_("С нашего счета"),
    # )
    contractor_bank_account = models.ForeignKey(
        "catalogs.ContractorBankAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Счет контрагента"),
    )
    transaction_id = models.CharField(_("ID транзакции"), max_length=100, blank=True)

    class Meta:
        abstract = True


class PaymentOrderOut(BaseBankPayment):
    """Исходящий платеж (поставщику или возврат покупателю)"""

    class Category(models.TextChoices):
        GOODS = "goods", _("За товары")
        SERVICES = "services", _("За услуги")
        CUSTOMER_REFUND = "customer_refund", _("Возврат покупателю")

    payment_number = models.CharField(
        _("Номер платёжного документа"), max_length=100
    )
    verification_code = models.CharField(
        _("Код проверки"), max_length=255, blank=True
    )
    uetr = models.UUIDField(_("UETR СЕП"), blank=True, null=True)

    category = models.CharField(
        _("Категория платежа"),
        max_length=20,
        choices=Category.choices,
        default=Category.GOODS,
    )

    our_bank_account = models.ForeignKey(
        "catalogs.OurBankAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="outgoing_payments",
        verbose_name=_("С нашего счета"),
    )

    # Связывается со счетами поставщиков
    purchase_invoices = models.ManyToManyField(
        "PurchaseInvoice",
        through="PaymentOutItem",
        verbose_name=_("Оплаченные счета поставщиков"),
    )

    # Комиссия только здесь
    bank_commission = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, verbose_name=_("Комиссия банка")
    )

    # Сумма, которая РЕАЛЬНО спишется с нашего счета
    total_debited = models.GeneratedField(
        expression=F("amount") + F("bank_commission"),
        output_field=models.DecimalField(max_digits=12, decimal_places=2),
        db_persist=True,
        verbose_name=_("Всего списано"),
    )

    def clean(self):
        super().clean()
        if (
            self.contractor_bank_account_id
            and self.contractor_id
            and self.contractor_bank_account.contractor_id != self.contractor_id
        ):
            raise ValidationError(
                {
                    "contractor_bank_account": _(
                        "Банковский счет не принадлежит выбранному контрагенту."
                    )
                }
            )

        if (
            self.our_bank_account_id
            and self.organization_id
            and self.our_bank_account.organization_id != self.organization_id
        ):
            raise ValidationError(
                {
                    "our_bank_account": _(
                        "Банковский счет не принадлежит выбранной организации."
                    )
                }
            )

        if self.is_applied and not self.contractor_bank_account_id and self.contractor_id:
            accounts = self.contractor.bank_accounts.all()
            if not accounts.exists():
                raise ValidationError(
                    {
                        "is_applied": _(
                            "Невозможно провести платеж — у контрагента нет банковских счетов."
                        )
                    }
                )
            if not self.contractor.primary_account_id:
                raise ValidationError(
                    {
                        "contractor_bank_account": _(
                            "У контрагента есть банковские счета, но основной счет не выбран."
                        )
                    }
                )

        if not self.is_applied or self.our_bank_account_id or not self.organization_id:
            return

        accounts = self.organization.our_accounts.all()
        if accounts.filter(is_default=True).exists():
            return
        if not accounts.exists():
            raise ValidationError(
                {
                    "is_applied": _(
                        "Невозможно провести платеж — у организации нет банковских счетов."
                    )
                }
            )
        raise ValidationError(
            {
                "our_bank_account": _(
                    "У организации есть банковские счета, но основной счет не выбран."
                )
            }
        )

    def save(self, *args, **kwargs):
        if not self.our_bank_account_id and self.organization_id:
            self.our_bank_account = self.organization.our_accounts.filter(
                is_default=True
            ).first()
        if not self.contractor_bank_account_id and self.contractor_id:
            self.contractor_bank_account = self.contractor.bank_accounts.filter(
                pk=self.contractor.primary_account_id
            ).first()
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = _("Платеж исходящий")
        verbose_name_plural = _("Платежи исходящие")


class PaymentOutItem(models.Model):
    sort_order = models.PositiveIntegerField(
        _("Порядок"), default=0, blank=True, null=True, db_index=True
    )
    payment = models.ForeignKey(PaymentOrderOut, on_delete=models.CASCADE)
    invoice = models.ForeignKey(PurchaseInvoice, on_delete=models.CASCADE)

    # Сумма, которую мы относим на этот конкретный счет
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("Сумма оплаты"),
    )

    def save(self, *args, resolve_amount=True, **kwargs):
        if resolve_amount and (self.amount is None or self.amount == 0) and self.invoice_id:
            invoice_total = self.invoice.items.aggregate(
                total=models.Sum(F("quantity") * F("order_item__purchase_price"))
            )["total"] or Decimal("0.00")
            paid_total = (
                PaymentOutItem.objects.filter(
                    invoice_id=self.invoice_id,
                    payment__is_applied=True,
                )
                .exclude(pk=self.pk)
                .aggregate(total=models.Sum("amount"))["total"]
                or Decimal("0.00")
            )
            self.amount = max(invoice_total - paid_total, Decimal("0.00"))

        super().save(*args, **kwargs)

    class Meta:
        verbose_name = _("Оплата счета")
        verbose_name_plural = _("Оплата счетов")
        ordering = ("sort_order", "pk")


class GoodsReceipt(BaseDocumentModel):
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.PROTECT, related_name="goods_receipts",
        verbose_name=_("Заказ поставщику"),
    )
    warehouse = models.ForeignKey(
        "catalogs.Warehouse", on_delete=models.PROTECT,
        verbose_name=_("Склад приёмки"),
    )
    comment = models.TextField(_("Комментарий"), blank=True)

    def clean(self):
        super().clean()
        if not self.purchase_order_id:
            return
        order = self.purchase_order
        if not order.is_applied or order.to_remove:
            raise ValidationError({"purchase_order": _("Выберите проведённый заказ поставщику без пометки на удаление.")})
        if order.organization_id and self.organization_id != order.organization_id:
            raise ValidationError({"organization": _("Организация поступления должна совпадать с организацией заказа.")})
        if self.is_applied and self.to_remove:
            raise ValidationError({"to_remove": _("Проведённое поступление нельзя пометить на удаление.")})

    class Meta(BaseDocumentModel.Meta):
        verbose_name = _("Поступление товаров")
        verbose_name_plural = _("Поступления товаров")


class GoodsReceiptItem(models.Model):
    receipt = models.ForeignKey(
        GoodsReceipt, on_delete=models.CASCADE, related_name="items",
    )
    order_item = models.ForeignKey(
        OrderItem, on_delete=models.PROTECT, related_name="receipt_items",
        verbose_name=_("Строка заказа поставщику"),
    )
    quantity = models.DecimalField(
        _("Получено"), max_digits=14, decimal_places=6,
        validators=[MinValueValidator(Decimal("0.000001"))],
    )
    @property
    def total_price(self):
        price = self.order_item.purchase_price
        if price is None or self.quantity is None:
            return None
        return (self.quantity * price).quantize(Decimal("0.01"))

    sort_order = models.PositiveIntegerField(
        _("Порядок"), default=0, blank=True, null=True, db_index=True,
    )

    def clean(self):
        super().clean()
        if not self.order_item_id:
            return
        item = self.order_item
        if self.quantity is not None:
            places = max(0, -self.quantity.normalize().as_tuple().exponent)
            if places > item.product.unit.decimal_places:
                raise ValidationError({"quantity": _("Количество не соответствует точности единицы измерения товара.")})
        if self.receipt_id or "receipt" in self._state.fields_cache:
            receipt = self.receipt
            if item.purchase_order_id != receipt.purchase_order_id:
                raise ValidationError({"order_item": _("Строка не принадлежит выбранному заказу поставщику.")})
            organization_id = item.organization_id or (
                item.purchase_order.organization_id if item.purchase_order_id else None
            )
            if organization_id != receipt.organization_id:
                raise ValidationError({"order_item": _("Организация строки заказа не совпадает с поступлением.")})

    class Meta:
        verbose_name = _("Позиция поступления")
        verbose_name_plural = _("Позиции поступления")
        ordering = ("sort_order", "pk")
        constraints = [
            models.UniqueConstraint(fields=("receipt", "order_item"), name="unique_receipt_order_item"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="receipt_quantity_positive"),
        ]
