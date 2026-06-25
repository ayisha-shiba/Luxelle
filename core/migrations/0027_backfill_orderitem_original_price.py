# Backfill original_price (MRP snapshot) on existing order items so that
# recalculate_totals() produces correct discounts for pre-existing orders.

from django.db import migrations


def backfill_original_price(apps, schema_editor):
    OrderItem = apps.get_model("core", "OrderItem")
    for item in OrderItem.objects.select_related("variant").all():
        # Prefer the live variant's MRP; fall back to the sale price already
        # snapshotted on the item (yields a zero discount, which is safe).
        if item.variant is not None and item.variant.original_price:
            item.original_price = item.variant.original_price
        else:
            item.original_price = item.unit_price
        item.save(update_fields=["original_price"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0026_orderitem_original_price"),
    ]

    operations = [
        migrations.RunPython(backfill_original_price, noop),
    ]
