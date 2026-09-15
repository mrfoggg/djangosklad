from django.db import migrations


def seed_roles(apps, schema_editor):
    role = apps.get_model("catalogs", "ContactRole")
    for code, name in (
        ("director", "Директор"),
        ("accountant", "Бухгалтер"),
        ("purchaser", "Закупщик"),
        ("parcel_recipient", "Получатель посылки"),
        ("end_user", "Конечный пользователь"),
    ):
        role.objects.using(schema_editor.connection.alias).get_or_create(code=code, defaults={"name": name})


class Migration(migrations.Migration):
    dependencies = [("catalogs", "0040_contactperson_contactrole_phonenumber_and_more")]
    operations = [migrations.RunPython(seed_roles, migrations.RunPython.noop)]
