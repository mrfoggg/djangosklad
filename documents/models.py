from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, GeneratedField
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from djmoney.models.fields import MoneyField
from unfold.views import BaseAutocompleteView

from catalogs.models import Organization


def get_default_organization_id():
    # Используем .values_list, чтобы вытащить только ID и не грузить весь объект
    org_id = (
        Organization.objects.filter(is_default=True)
        .values_list("id", flat=True)
        .first()
    )
    return org_id


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

    created = models.DateTimeField(
        verbose_name=_("Создан"), auto_now_add=True, db_index=True
    )
    updated = models.DateTimeField(verbose_name=_("Изменен"), auto_now=True)

    class Meta:
        verbose_name = _("Позиция прайса")
        verbose_name_plural = _("Позиции прайса")


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
    comment = models.TextField(_("Комментарий"), blank=True)

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

    product = models.ForeignKey(
        "catalogs.Product",
        on_delete=models.PROTECT,
        related_name="order_items",
        verbose_name=_("Товар"),
    )
    # КОЛИЧЕСТВО И ЦЕНА
    price = models.DecimalField(
        max_digits=10, decimal_places=2, verbose_name=_("Цена за единицу")
    )
    quantity = models.DecimalField(
        max_digits=10, decimal_places=2, default=1.00, verbose_name=_("Количество")
    )
    # ВЫЧИСЛЯЕМОЕ ПОЛЕ
    total_price = models.GeneratedField(
        expression=F("quantity") * F("price"),
        output_field=models.DecimalField(max_digits=10, decimal_places=2),
        db_persist=True,
        verbose_name=_("Сумма"),
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

    class Meta:
        verbose_name = _("Товар в заказе")
        verbose_name_plural = _("Товары в заказе")
        # По умолчанию сортируем по обоим полям (Django применит их по приоритету)
        ordering = ["sort_order_purchase", "sort_order_customer"]

    def __str__(self):
        return f"{self.product.name} ({self.quantity})"

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
        "catalogs.BankAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("Счет для оплаты"),
    )
    note = models.CharField(_("Примечание"), max_length=255, blank=True)

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
        self.full_clean()
