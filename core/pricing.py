from decimal import Decimal, ROUND_HALF_UP

CGST_RATE = Decimal("0.09")
SGST_RATE = Decimal("0.09")
GST_RATE  = CGST_RATE + SGST_RATE     # 0.18


def money(value):
    """Round any number to 2 decimal places, the way currency should."""
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def compute(subtotal, mrp_total=None, shipping=Decimal("0"), coupon_discount=Decimal("0")):
    subtotal = money(subtotal)
    mrp_total = money(subtotal if mrp_total is None else mrp_total)
    shipping = money(shipping)

    # Coupon reduces the taxable amount; GST is charged on the post-coupon value.
    coupon_discount = money(min(Decimal(coupon_discount), subtotal))
    taxable = money(subtotal - coupon_discount)

    cgst = money(taxable * CGST_RATE)
    sgst = money(taxable * SGST_RATE)
    gst  = money(cgst + sgst)

    return {
        "mrp_subtotal":    mrp_total,                     # MRP total before any discount
        "subtotal":        subtotal,                      # post-offer price (taxable base)
        "discount":        money(mrp_total - subtotal),   # offer discount
        "coupon_discount": coupon_discount,
        "taxable":         taxable,
        "cgst":            cgst,
        "sgst":            sgst,
        "gst":             gst,
        "shipping":        shipping,
        "grand_total":     money(taxable + gst + shipping),
    }


def summarize_items(items, shipping=Decimal("0"), coupon_discount=Decimal("0")):
    """Compute the breakdown from cart/order line items.

    `items` is any iterable whose rows expose:
        .total_price -> sale_price * quantity
        .subtotal    -> original_price * quantity (MRP)
    Both CartItem and OrderItem-style rows satisfy this.
    """
    subtotal  = sum((Decimal(i.total_price) for i in items), Decimal("0"))
    mrp_total = sum((Decimal(i.subtotal)    for i in items), Decimal("0"))
    return compute(subtotal, mrp_total, shipping, coupon_discount)
