"""Read-only supplier order totals from source lines and posted documents."""
from decimal import Decimal

from django.db.models import Prefetch, Q, Sum

from .models import GoodsReceipt, GoodsReceiptItem, InvoiceItem, PaymentOutItem, PurchaseInvoice, SalesDocument, SalesDocumentItem, SalesInvoice, SalesInvoiceItem, PaymentInItem

ZERO = Decimal("0")


def supplier_order_summary(order):
    return _order_summary(order)


def customer_order_summary(order):
    return _order_summary(order, customer=True)


def _order_summary(order, customer=False):
    receipt_model = SalesDocument if customer else GoodsReceipt
    receipt_item_model = SalesDocumentItem if customer else GoodsReceiptItem
    invoice_model = SalesInvoice if customer else PurchaseInvoice
    invoice_item_model = SalesInvoiceItem if customer else InvoiceItem
    payment_item_model = PaymentInItem if customer else PaymentOutItem
    order_field = "customer_order" if customer else "purchase_order"
    price_field = "customer_price" if customer else "purchase_price"
    party_field = "customer" if customer else "supplier"
    receipt_field = "document" if customer else "receipt"
    lines = list(order.items.select_related("product__unit"))
    received = dict(receipt_item_model.objects.filter(
        **{f"order_item__{order_field}": order, f"{receipt_field}__is_applied": True},
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
            if quantity and getattr(line, price_field) is None:
                amounts[kind] = None
            elif amounts[kind] is not None and getattr(line, price_field) is not None:
                amounts[kind] += quantity * getattr(line, price_field)

    invoices = list(invoice_model.objects.filter(
        Q(orders=order) | Q(**{f"items__order_item__{order_field}": order}),
    ).distinct().order_by("pk").select_related(party_field).prefetch_related(Prefetch(
        "items", queryset=invoice_item_model.objects.select_related("order_item"),
    )))
    receipts = list(receipt_model.objects.filter(
        Q(orders=order) | Q(**{f"items__order_item__{order_field}": order}),
    ).distinct().order_by("pk").select_related(party_field).prefetch_related(Prefetch(
        "items", queryset=receipt_item_model.objects.select_related("order_item"),
    )))
    payments = dict(payment_item_model.objects.filter(
        invoice_id__in=[invoice.pk for invoice in invoices], payment__is_applied=True,
    ).values("invoice_id").annotate(total=Sum("amount")).values_list("invoice_id", "total"))
    for document in [*invoices, *receipts]:
        document_lines = list(document.items.all())
        if any(getattr(line.order_item, price_field) is None for line in document_lines):
            document.summary_total = None
        else:
            document.summary_total = sum(
                (line.quantity * getattr(line.order_item, price_field) for line in document_lines), ZERO,
            ).quantize(Decimal("0.01"))
    for invoice in invoices:
        invoice.summary_paid = payments.get(invoice.pk, ZERO).quantize(Decimal("0.01"))

    paid = ZERO
    estimated = False
    for invoice in invoices:
        payment = payments.get(invoice.pk, ZERO)
        if not payment:
            continue
        invoice_lines = list(invoice.items.all())
        own_lines = [line for line in invoice_lines if getattr(line.order_item, f"{order_field}_id") == order.pk]
        if not own_lines:
            continue
        shared = any(getattr(line.order_item, f"{order_field}_id") != order.pk for line in invoice_lines)
        if not shared:
            paid += payment
            continue
        estimated = True
        if any(getattr(line.order_item, price_field) is None for line in invoice_lines):
            paid = None
            break
        total = sum((line.quantity * getattr(line.order_item, price_field) for line in invoice_lines), ZERO)
        subtotal = sum((line.quantity * getattr(line.order_item, price_field) for line in own_lines), ZERO)
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
