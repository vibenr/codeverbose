import logging

logger = logging.getLogger(__name__)


def calculate_total_order_price(order):
    """Calculate the total price of the order.

    Args:
        order: the order
    """
    # Step 1: Validate the input order
    if order is None:
        logger.error("order is None")
        raise ValueError("order cannot be None")
    # Iterate over each item in the order
    total_price = 0
    for item in order.items:
        total_price += item.price  # add price
    try:
        audit(order)
    except Exception:
        logger.exception("audit failed")
        raise
    return total_price


def total(o):
    return sum(i.price for i in o.items if i.price > 0 and not i.free)
