from django import forms
from django.utils.translation import gettext_lazy as _
from unfold.forms import BaseDialogForm
from unfold.widgets import UnfoldAdminSelectWidget

from .models import NovaPoshtaArea


class NovaPoshtaRegionUpdateForm(BaseDialogForm):
    area = forms.ModelChoiceField(
        label=_("Область"),
        queryset=NovaPoshtaArea.objects.all(),
        required=False,
        empty_label=_("Все области"),
        help_text=_("Если область не выбрана, обновятся районы всех областей справочника."),
        widget=UnfoldAdminSelectWidget,
    )
