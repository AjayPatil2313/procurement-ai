from decimal import Decimal
from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.companies.models import Company
from apps.billing.models import CompanyWallet, CompanyCreditWallet


@receiver(post_save, sender=Company)
def initialize_company_wallet_on_creation(sender, instance, created, **kwargs):
    """
    Ensures every company receives an independent prepaid CompanyWallet initialized
    with ₹0.00 available balance immediately upon registration.
    Also ensures legacy CompanyCreditWallet exists for seamless backward compatibility.
    """
    if created:
        CompanyWallet.objects.get_or_create(
            company=instance,
            defaults={
                "available_balance": Decimal("0.00"),
                "reserved_balance": Decimal("0.00"),
                "total_credited": Decimal("0.00"),
                "total_spent": Decimal("0.00"),
                "currency": "INR",
                "status": CompanyWallet.WalletStatus.ACTIVE,
            },
        )
        CompanyCreditWallet.objects.get_or_create(
            company=instance,
            defaults={
                "subscription_credits": 50,
                "purchased_credits": 0,
                "promotional_credits": 0,
            },
        )
