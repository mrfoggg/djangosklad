from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0046_paymentorderout_category"),
    ]

    operations = [
        migrations.AddField(
            model_name="paymentorderout",
            name="payment_number",
            field=models.CharField(
                default="",
                max_length=100,
                verbose_name="Номер платёжного документа",
            ),
            preserve_default=False,
        ),
    ]
