from decimal import Decimal

from django.db import models
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
AVAILABLE_CURRENCIES = [("UAH", "UAH"), ("USD", "USD")]


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
        default_currency="UAH",
        currency_choices=AVAILABLE_CURRENCIES,
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


class PurchaseOrderItem(models.Model):
    """
    Позиция в заказе поставщику.
    """

    document = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name=_("Документ"),
    )
    product = models.ForeignKey(
        "catalogs.Product", on_delete=models.PROTECT, verbose_name=_("Товар")
    )
    quantity = models.DecimalField(
        _("Количество"), max_digits=10, decimal_places=3, default=1
    )
    price = MoneyField(
        max_digits=12,
        decimal_places=2,
        default_currency="UAH",
        currency_choices=AVAILABLE_CURRENCIES,
        verbose_name=_("Цена за ед."),
    )

    class Meta:
        verbose_name = _("Позиция заказа")
        verbose_name_plural = _("Позиции заказа")

    @property
    def total_amount(self):
        return self.price * self.quantity

    def __str__(self):
        return f"{self.product} ({self.quantity} шт.)"
