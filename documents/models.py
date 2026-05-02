from decimal import Decimal

from django.db import models
from django.db.models import F, GeneratedField
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from djmoney.models.fields import MoneyField


class BaseDocumentModel(models.Model):
    """
    Абстрактная база для всех документов.
    """

    dt_created = models.DateTimeField(
        verbose_name=_("Создан"), auto_now_add=True, db_index=True
    )
    dt_updated = models.DateTimeField(verbose_name=_("Изменен"), auto_now=True)

    # Флаг проведения документа
    is_applied = models.BooleanField(
        verbose_name=_("Проведен"),
        default=False,
        db_index=True,
        help_text=_("Документ проведен"),
    )
    dt_applied = models.DateTimeField(
        verbose_name=_("Дата проведения"), null=True, blank=True, editable=False
    )

    to_remove = models.BooleanField(
        verbose_name=_("Помечен на удаление"), default=False, db_index=True
    )

    def save(self, *args, **kwargs):
        # Если флаг "Проведен" установлен, а дата проведения еще не задана
        if self.is_applied and not self.dt_applied:
            self.dt_applied = timezone.now()
        # Опционально: если сняли флаг проведения, можно очищать дату
        elif not self.is_applied and self.dt_applied:
            self.dt_applied = None

        super().save(*args, **kwargs)

    class Meta:
        abstract = True
        ordering = ["-dt_created"]

    def __str__(self):
        # Форматируем даты заранее для удобства
        created_str = self.dt_created.strftime("%d.%m.%Y")

        if self.is_applied:
            # Если дата проведения по какой-то причине пуста (редкий случай),
            # подстрахуемся и выведем хотя бы дату создания
            applied_str = (
                self.dt_applied.strftime("%d.%m.%Y") if self.dt_applied else created_str
            )
            return f"{self._meta.verbose_name} №{self.id} (Проведен {applied_str})"

        # Если не проведен — выводим статус Черновик и дату создания
        return f"{self._meta.verbose_name} №{self.id} от {created_str} (Черновик)"


# Набор доступных валют
# AVAILABLE_CURRENCIES = [("UAH", "UAH"), ("USD", "USD")]


class SupplierPriceList(BaseDocumentModel):
    supplier = models.ForeignKey(
        "catalogs.Contractor",  # Ссылка на Contractor в catalogs
        on_delete=models.CASCADE,
        limit_choices_to={"is_supplier": True},
        verbose_name=_("Поставщик"),
        related_name="price_lists",
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

    # class PurchaseOrderItem(models.Model):
    #     """
    #     Позиция в заказе поставщику.
    #     Всегда в UAH.
    #     """

    #     document = models.ForeignKey(
    #         PurchaseOrder,
    #         on_delete=models.CASCADE,
    #         related_name="items",
    #         verbose_name=_("Документ"),
    #     )
    #     product = models.ForeignKey(
    #         "catalogs.Product", on_delete=models.PROTECT, verbose_name=_("Товар")
    #     )
    #     quantity = models.DecimalField(
    #         _("Количество"), max_digits=10, decimal_places=3, default=1
    #     )
    #     # Заменяем MoneyField на обычный Decimal
    #     price = models.DecimalField(
    #         max_digits=12,
    #         decimal_places=2,
    #         default=0,
    #         verbose_name=_("Цена за ед. (UAH)"),
    #     )

    #     class Meta:
    #         verbose_name = _("Позиция заказа")
    #         verbose_name_plural = _("Позиции заказа")

    # @property
    # def total_amount(self):
    #     # Теперь это простое умножение Decimal на Decimal
    #     return round(self.price * self.quantity, 2)

    # def __str__(self):
    #     return f"{self.product} ({self.quantity})"


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
        # Добавляем фильтрацию: выбираем только тех, кто помечен как покупатель
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
        ordering = ["-dt_created"]

    def __str__(self):
        # Используем метод или атрибут имени у Contractor (например, name или last_name)
        return f"{_('Заказ')} №{self.id} - {self.customer}"


# class CustomerOrderItem(models.Model):
#     document = models.ForeignKey(
#         CustomerOrder, related_name="items", on_delete=models.CASCADE
#     )
#     product = models.ForeignKey(
#         "catalogs.Product",
#         on_delete=models.PROTECT,
#         verbose_name=_("Товар"),
#     )
#     quantity = models.PositiveIntegerField(default=1, verbose_name=_("Кол-во"))
#     # Используем MoneyField или DecimalField для цены продажи
#     price = models.DecimalField(
#         max_digits=10, decimal_places=2, verbose_name=_("Цена продажи")
#     )

#     class Meta:
#         verbose_name = _("Товар в заказе")
#         verbose_name_plural = _("Товары в заказе")


class OrderItem(models.Model):
    """Строка товара, универсальная для закупок и продаж. Позволяет реализовать механим резервирования"""

    # СВЯЗИ С ЗАКАЗАМИ
    purchase_order = models.ForeignKey(
        "PurchaseOrder",
        on_delete=models.CASCADE,
        related_name="items",
        null=True,
        blank=True,
        verbose_name=_("Заказ поставщику"),
    )
    customer_order = models.ForeignKey(
        "CustomerOrder",
        on_delete=models.CASCADE,
        related_name="items",
        null=True,
        blank=True,
        verbose_name=_("Заказ покупателя"),
    )

    # ТОВАР И СКЛАД
    product = models.ForeignKey(
        "catalogs.Product",
        on_delete=models.PROTECT,
        related_name="order_items",
        verbose_name=_("Товар"),
    )
    warehouse = models.ForeignKey(
        "catalogs.Warehouse",
        on_delete=models.PROTECT,
        related_name="order_items",
        verbose_name=_("Склад"),
        help_text=_("Склад отгрузки или приемки для этой строки"),
    )

    # КОЛИЧЕСТВО И ЦЕНА
    quantity = models.DecimalField(
        max_digits=10, decimal_places=2, verbose_name=_("Количество")
    )
    price = models.DecimalField(
        max_digits=10, decimal_places=2, verbose_name=_("Цена за единицу")
    )

    # ВЫЧИСЛЯЕМОЕ ПОЛЕ
    total_price = models.GeneratedField(
        expression=F("quantity") * F("price"),
        output_field=models.DecimalField(max_digits=10, decimal_places=2),
        db_persist=True,
        verbose_name=_("Сумма"),
    )

    # СОРТИРОВКА
    sort_order_purchase = models.PositiveIntegerField(
        default=0, verbose_name=_("Порядок в закупке")
    )
    sort_order_customer = models.PositiveIntegerField(
        default=0, verbose_name=_("Порядок в продаже")
    )

    class Meta:
        verbose_name = _("Товар в заказе")
        verbose_name_plural = _("Товары в заказе")
        # По умолчанию сортируем по обоим полям (Django применит их по приоритету)
        ordering = ["sort_order_purchase", "sort_order_customer"]

    def __str__(self):
        return f"{self.product.name} ({self.quantity})"
