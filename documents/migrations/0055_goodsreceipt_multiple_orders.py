import django.db.models.deletion
from django.db import migrations, models


def transfer_orders(apps, schema_editor):
    Receipt = apps.get_model("documents", "GoodsReceipt")
    database = schema_editor.connection.alias
    for receipt in Receipt.objects.using(database).select_related("purchase_order").iterator():
        receipt.supplier_id = receipt.purchase_order.supplier_id
        receipt.save(using=database, update_fields=["supplier"])
        receipt.orders.add(receipt.purchase_order_id)


class Migration(migrations.Migration):
    dependencies = [("documents", "0054_purchaseinvoice_supplier_invoice_date_and_more")]
    operations = [
        migrations.AddField(
            model_name="goodsreceipt", name="supplier",
            field=models.ForeignKey(
                to="catalogs.contractor", on_delete=django.db.models.deletion.PROTECT,
                limit_choices_to={"is_supplier": True}, related_name="goods_receipts",
                verbose_name="Поставщик", null=True,
            ),
        ),
        migrations.AddField(
            model_name="goodsreceipt", name="orders",
            field=models.ManyToManyField(
                to="documents.purchaseorder", related_name="goods_receipts", blank=True,
                verbose_name="Основание: Заказы поставщику",
            ),
        ),
        migrations.RunPython(transfer_orders, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="goodsreceipt", name="supplier",
            field=models.ForeignKey(
                to="catalogs.contractor", on_delete=django.db.models.deletion.PROTECT,
                limit_choices_to={"is_supplier": True}, related_name="goods_receipts",
                verbose_name="Поставщик",
            ),
        ),
        migrations.RemoveField(model_name="goodsreceipt", name="purchase_order"),
    ]
