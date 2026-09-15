from django import forms
from django.utils.translation import gettext_lazy as _
from unfold.forms import BaseDialogForm
from unfold.widgets import UnfoldAdminSelectWidget, UnfoldAdminTextInputWidget
from phonenumber_field.formfields import SplitPhoneNumberField

from .models import NovaPoshtaArea, PhoneNumber


class NovaPoshtaRegionUpdateForm(BaseDialogForm):
    area = forms.ModelChoiceField(
        label=_("Область"),
        queryset=NovaPoshtaArea.objects.all(),
        required=False,
        empty_label=_("Все области"),
        help_text=_("Если область не выбрана, обновятся районы всех областей справочника."),
        widget=UnfoldAdminSelectWidget,
    )


class NovaPoshtaSettlementUpdateForm(BaseDialogForm):
    area = forms.ModelChoiceField(
        label=_("Область"),
        queryset=NovaPoshtaArea.objects.all(),
        required=False,
        empty_label=_("Все области"),
        help_text=_("Без выбора области обновятся все населённые пункты. Загрузка может занять несколько минут."),
        widget=UnfoldAdminSelectWidget,
    )


class AdminSplitPhoneNumberField(SplitPhoneNumberField):
    def prefix_field(self):
        field = super().prefix_field()
        field.widget = UnfoldAdminSelectWidget(choices=field.choices)
        return field

    def number_field(self):
        field = super().number_field()
        field.widget = UnfoldAdminTextInputWidget(attrs={"autocomplete": "tel-national", "data-phone-country-mask": "true"})
        field.widget.input_type = "tel"
        return field


class PhoneNumberForm(forms.ModelForm):
    class Media:
        js = ("catalogs/js/phone_number.js",)

    class Meta:
        model = PhoneNumber
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "number" in self.fields:
            # Создаём поле при открытии формы: регион берётся из settings.
            self.fields["number"] = AdminSplitPhoneNumberField(
                label=_("Номер телефона"),
                max_length=PhoneNumber._meta.get_field("number").max_length,
            )
