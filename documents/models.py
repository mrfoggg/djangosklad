from decimal import Decimal

from django.db import models
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

    class Meta:
        abstract = True
        ordering = ["-dt_created"]

    def __str__(self):
        status = _("Проведен") if self.is_applied else _("Черновик")
        return f"{self._meta.verbose_name} №{self.id} ({status})"


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
