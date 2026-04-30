from django.db import models
from django.utils.translation import gettext_lazy as _

class BaseDocumentModel(models.Model):
    """
    Абстрактная база для всех документов.
    """
    number = models.CharField(
        max_length=50,
        verbose_name=_('Номер'),
        db_index=True
    )
    dt_created = models.DateTimeField(
        verbose_name=_('Создан'),
        auto_now_add=True,
        db_index=True
    )
    dt_updated = models.DateTimeField(
        verbose_name=_('Изменен'),
        auto_now=True
    )

    # Флаг применения документа к учету
    is_applied = models.BooleanField(
        verbose_name=_('Применен'),
        default=False,
        db_index=True,
        help_text=_("Документ проведен и учитывается в остатках")
    )
    dt_applied = models.DateTimeField(
        verbose_name=_('Дата применения'),
        null=True,
        blank=True,
        editable=False
    )

    to_remove = models.BooleanField(
        verbose_name=_('В корзине'),
        default=False,
        db_index=True
    )

    class Meta:
        abstract = True
        ordering = ['-dt_created']

    def __str__(self):
        status = _("Применен") if self.is_applied else _("Черновик")
        return f"{self._meta.verbose_name} №{self.number} ({status})"
