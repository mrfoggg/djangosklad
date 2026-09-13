"""Read-only supplier order totals from source lines and posted documents."""
from decimal import Decimal

from django.db.models import Prefetch, Q, Sum

from .models import GoodsReceipt, GoodsReceiptItem, InvoiceItem, PaymentOutItem, PurchaseInvoice

ZERO = Decimal("0")


def supplier_order_summary(order):
    lines = list(order.items.select_related("product__unit"))
    received = dict(GoodsReceiptItem.objects.filter(
        order_item__purchase_order=order, receipt__is_applied=True,
    ).values("order_item_id").annotate(total=Sum("quantity")).values_list("order_item_id", "total"))
    quantities = {}
    amounts = {"ordered": ZERO, "received": ZERO, "pending": ZERO}
    for line in lines:
        unit = line.product.unit
        group = quantities.setdefault(unit.pk, {"symbol": unit.symbol, "places": unit.decimal_places,
                                               "ordered": ZERO, "received": ZERO, "pending": ZERO})
        counts = {"ordered": line.quantity, "received": received.get(line.pk, ZERO)}
        counts["pending"] = max(line.quantity - counts["received"], ZERO)
        for kind, quantity in counts.items():
            group[kind] += quantity
            if quantity and line.purchase_price is None:
                amounts[kind] = None
            elif amounts[kind] is not None and line.purchase_price is not None:
                amounts[kind] += quantity * line.purchase_price

    invoices = list(PurchaseInvoice.objects.filter(
        Q(orders=order) | Q(items__order_item__purchase_order=order),
    ).distinct().order_by("pk").prefetch_related(Prefetch(
        "items", queryset=InvoiceItem.objects.select_related("order_item"),
    )))
    receipts = list(GoodsReceipt.objects.filter(
        Q(orders=order) | Q(items__order_item__purchase_order=order),
    ).distinct().order_by("pk"))
    payments = dict(PaymentOutItem.objects.filter(
        invoice_id__in=[invoice.pk for invoice in invoices], payment__is_applied=True,
    ).values("invoice_id").annotate(total=Sum("amount")).values_list("invoice_id", "total"))
    paid = ZERO
    estimated = False
    for invoice in invoices:
        payment = payments.get(invoice.pk, ZERO)
        if not payment:
            continue
        invoice_lines = list(invoice.items.all())
        own_lines = [line for line in invoice_lines if line.order_item.purchase_order_id == order.pk]
        if not own_lines:
            continue
        shared = any(line.order_item.purchase_order_id != order.pk for line in invoice_lines)
        if not shared:
            paid += payment
            continue
        estimated = True
        if any(line.order_item.purchase_price is None for line in invoice_lines):
            paid = None
            break
        total = sum((line.quantity * line.order_item.purchase_price for line in invoice_lines), ZERO)
        subtotal = sum((line.quantity * line.order_item.purchase_price for line in own_lines), ZERO)
        if total <= 0:
            paid = None
            break
        paid += payment * subtotal / total

    for kind, amount in amounts.items():
        amounts[kind] = amount.quantize(Decimal("0.01")) if amount is not None else None
    if paid is not None:
        paid = paid.quantize(Decimal("0.01"))
    balance = amounts["ordered"] - paid if amounts["ordered"] is not None and paid is not None else None
    return {"quantities": list(quantities.values()), "amounts": amounts,
            "invoices": invoices, "receipts": receipts, "paid": paid,
            "estimated": estimated, "balance": balance}
