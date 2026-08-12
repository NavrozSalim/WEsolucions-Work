from django.apps import AppConfig


class StoresConfig(AppConfig):
    name = 'stores'

    def ready(self):
        from stores import signals  # noqa: F401
