from django.core.management.base import BaseCommand

from apps.companies.models import CompanyPermission


class Command(BaseCommand):

    help = "Create default company permissions"

    MODULES = [
    "companies",
    "members",
    "categories",
    "requirements",
    "products",
    "search",
    "results",
    "external_companies",
    "inquiries",
    "saved_items",
    "dashboard",
]

    PERMISSIONS = [
        "READ",
        "EDIT",
        "DELETE",
        "UPDATE",
        "IMPORT",
        "EXPORT",
    ]

    def handle(self, *args, **options):

        created_count = 0

        for module in self.MODULES:
            for permission in self.PERMISSIONS:

                _, created = CompanyPermission.objects.get_or_create(
                    module=module,
                    permission=permission,
                )

                if created:
                    created_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"{created_count} permissions created successfully."
            )
        )