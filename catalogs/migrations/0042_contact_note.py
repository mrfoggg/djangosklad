from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("catalogs", "0041_seed_contact_roles")]
    operations = [
        migrations.RenameField(model_name="contractorcontactperson", old_name="position", new_name="note"),
        migrations.AlterField(model_name="contractorcontactperson", name="note", field=models.CharField(blank=True, max_length=150, verbose_name="Примечание")),
    ]
