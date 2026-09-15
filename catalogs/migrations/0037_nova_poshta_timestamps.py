from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):
    dependencies = [("catalogs", "0036_novaposhtasettlementtype_and_more")]

    operations = [
        migrations.AddField(
            model_name="novaposhtaarea", name="created",
            field=models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Создан", default=timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="novaposhtaarea", name="updated",
            field=models.DateTimeField(auto_now=True, verbose_name="Изменен", default=timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="novaposhtaregion", name="created",
            field=models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Создан", default=timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="novaposhtaregion", name="updated",
            field=models.DateTimeField(auto_now=True, verbose_name="Изменен", default=timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="novaposhtasettlementtype", name="created",
            field=models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Создан", default=timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="novaposhtasettlementtype", name="updated",
            field=models.DateTimeField(auto_now=True, verbose_name="Изменен", default=timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="novaposhtasettlement", name="created",
            field=models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Создан", default=timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="novaposhtasettlement", name="updated",
            field=models.DateTimeField(auto_now=True, verbose_name="Изменен", default=timezone.now),
            preserve_default=False,
        ),
    ]
