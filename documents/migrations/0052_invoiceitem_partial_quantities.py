from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


def copy_order_quantities(apps, schema_editor):
    InvoiceItem = apps.get_model("documents", "InvoiceItem")
    for item in InvoiceItem.objects.using(schema_editor.connection.alias).select_related("order_item").iterator():
        item.quantity = item.order_item.quantity
        item.save(update_fields=["quantity"])


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0051_remove_goodsreceiptitem_receipt_price_nonnegative_and_more"),
    ]
    operations = [
        migrations.AlterField(
            model_name="invoiceitem", name="order_item",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="invoice_items", to="documents.orderitem", verbose_name="Строка заказа",
            ),
        ),
        migrations.AddField(
            model_name="invoiceitem", name="quantity",
            field=models.DecimalField(max_digits=14, decimal_places=6, null=True, blank=True, verbose_name="Количество по счёту"),
        ),
        migrations.RunPython(copy_order_quantities, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="invoiceitem", name="quantity",
            field=models.DecimalField(
                max_digits=14, decimal_places=6, blank=True, verbose_name="Количество по счёту",
                validators=[django.core.validators.MinValueValidator(Decimal("0.000001"))],
            ),
        ),
        migrations.AddConstraint(
            model_name="invoiceitem",
            constraint=models.UniqueConstraint(fields=("invoice", "order_item"), name="unique_invoice_order_item"),
        ),
        migrations.AddConstraint(
            model_name="invoiceitem",
            constraint=models.CheckConstraint(condition=models.Q(quantity__gt=0), name="invoice_quantity_positive"),
        ),
    ]
