from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CatalogsConfig(AppConfig):
    name = 'catalogs'
    verbose_name = _('Справочники')

    def ready(self):
        from django.db.backends.signals import connection_created
        from .collations import register_collations

        connection_created.connect(
            register_collations, dispatch_uid="catalogs.ukrainian_collation"
        )
